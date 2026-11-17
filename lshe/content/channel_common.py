"""
What lshe_video_chef.py (upload to Studio) and the offline builder
(prepare_media.py, build_channel.py) share: reading and checking the
manifest and topics CSVs, and making thumbnails.

Only the standard library and le_utils are imported here, so this runs both
in the lshe-content-tools image and in the Kolibri image. Pillow, ffmpeg and
ffprobe are needed only by the thumbnail functions.
"""
import collections
import csv
import hashlib
import json
import os
import re
import subprocess

from le_utils.constants import licenses

SOURCE_DOMAIN = "lshe.edulution"
BAND_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

LICENSES = {
    "cc by": licenses.CC_BY,
    "cc by-sa": licenses.CC_BY_SA,
    "cc by-nd": licenses.CC_BY_ND,
    "cc by-nc": licenses.CC_BY_NC,
    "cc by-nc-sa": licenses.CC_BY_NC_SA,
    "cc by-nc-nd": licenses.CC_BY_NC_ND,
    "all rights reserved": licenses.ALL_RIGHTS_RESERVED,
    "special permissions": licenses.SPECIAL_PERMISSIONS,
    "public domain": licenses.PUBLIC_DOMAIN,
}
# Studio refuses these without a copyright holder.
NEEDS_HOLDER = set(LICENSES.values()) - {licenses.PUBLIC_DOMAIN}

THUMB_W, THUMB_H = 640, 360  # 16:9, as Kolibri shows thumbnails


class ManifestError(Exception):
    pass


def row_key(row):
    """A video's id: its code, or its file if it has no code."""
    return (row.get("code") or "").strip() or row["file"]


def parse_license(row):
    """(le_utils licence id, copyright holder or None) for a manifest row."""
    name = re.sub(r"\s+", " ", (row.get("license") or "").strip().lower())
    name = name.replace("cc-", "cc ")
    # A version is fine ("CC BY-SA 4.0"): Kolibri records the licence type only.
    name = re.sub(r"\s+\d+(\.\d+)*$", "", name)
    if name not in LICENSES:
        raise ManifestError(
            "{}: license '{}' is not one of: {}".format(
                row.get("code") or row.get("file"),
                row.get("license") or "",
                ", ".join(
                    k.upper() if k.startswith("cc") else k.title() for k in LICENSES
                ),
            )
        )
    license_id = LICENSES[name]
    holder = (row.get("copyright_holder") or "").strip()
    if license_id in NEEDS_HOLDER and not holder:
        raise ManifestError(
            "{}: copyright_holder is empty".format(row.get("code") or row.get("file"))
        )
    return license_id, holder or None


def check_band(band):
    if not BAND_RE.match(band or ""):
        raise ManifestError("give a band, e.g. G4-5")
    return band


def read_manifest(path, videos, band):
    """The manifest rows for band, checked, ordered by code then title."""
    if not os.path.isfile(path):
        raise ManifestError("manifest not found: {}".format(path))
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    others = collections.Counter(
        (r.get("band") or "").strip() or "no band"
        for r in rows
        if (r.get("band") or "").strip() != band
    )
    chosen = [r for r in rows if (r.get("band") or "").strip() == band]
    if not chosen:
        raise ManifestError("no rows with band {} in {}".format(band, path))
    problems, seen = [], set()
    for row in chosen:
        key = row_key(row)
        if key in seen:
            problems.append("{}: listed twice".format(key))
        seen.add(key)
        if not os.path.isfile(os.path.join(videos, row["file"])):
            problems.append("{}: file not found: {}".format(key, row["file"]))
        if not (row.get("title") or "").strip():
            problems.append("{}: title is empty".format(key))
        try:
            parse_license(row)
        except ManifestError as e:
            problems.append(str(e))
    if problems:
        raise ManifestError("\n  ".join(["manifest problems:"] + problems))
    if others:
        print(
            "Left out of {}: {}".format(
                band, ", ".join("{} {}".format(n, b) for b, n in sorted(others.items()))
            )
        )
    return sorted(chosen, key=lambda r: ((r.get("code") or "").strip(), r["title"]))


def group_by_topic(rows):
    """{topic: [rows]} in the manifest's order; "" holds videos with no topic."""
    groups = collections.OrderedDict()
    for row in rows:
        groups.setdefault((row.get("topic") or "").strip(), []).append(row)
    return groups


def read_topics(path, band):
    """{topic: {"description": ..., "thumbnail": ...}} for the band."""
    if not path:
        return {}
    if not os.path.isfile(path):
        raise ManifestError("topics file not found: {}".format(path))
    base = os.path.dirname(os.path.abspath(path))
    topics = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("topic") or "").strip()
            row_band = (row.get("band") or "").strip()
            if not name or (row_band and row_band != band):
                continue
            # A band-specific row wins over a row for every band.
            if name in topics and not row_band:
                continue
            thumbnail = (row.get("thumbnail") or "").strip()
            if thumbnail:
                thumbnail = os.path.join(base, thumbnail)
                if not os.path.isfile(thumbnail):
                    raise ManifestError(
                        "{}: thumbnail not found: {}".format(name, thumbnail)
                    )
            topics[name] = {
                "description": (row.get("description") or "").strip(),
                "thumbnail": thumbnail or None,
            }
    return topics


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "topic"


def video_duration(video):
    """A video's length in seconds (float), from ffprobe."""
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            video,
        ]
    ).strip()
    return float(out or 0)


def video_frame(video, out, duration=None):
    """Save a frame from a quarter of the way into video, at THUMB_W x THUMB_H."""
    if duration is None:
        duration = video_duration(video)
    fit = (
        "scale={w}:{h}:force_original_aspect_ratio=decrease,"
        "pad={w}:{h}:(ow-iw)/2:(oh-ih)/2".format(w=THUMB_W, h=THUMB_H)
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", str(duration / 4), "-i", video]
        + ["-frames:v", "1", "-vf", fit, out],
        check=True,
    )


def tiled_thumbnail(videos, folder):
    """A PNG tiled from frames of up to four videos (one frame if fewer)."""
    from PIL import Image

    videos = videos[:4] if len(videos) >= 4 else videos[:1]
    key = hashlib.md5("\n".join(videos).encode("utf-8")).hexdigest()
    out = os.path.join(folder, "tile_{}.png".format(key))
    if os.path.isfile(out):
        return out
    os.makedirs(folder, exist_ok=True)
    frames = []
    for i, video in enumerate(videos):
        frame = os.path.join(folder, "frame_{}_{}.png".format(key, i))
        video_frame(video, frame)
        frames.append(frame)
    if len(frames) == 1:
        os.replace(frames[0], out)
        return out
    tile = Image.new("RGB", (THUMB_W, THUMB_H))
    half_w, half_h = THUMB_W // 2, THUMB_H // 2
    for i, frame in enumerate(frames):
        with Image.open(frame) as im:
            tile.paste(
                im.resize((half_w, half_h)), ((i % 2) * half_w, (i // 2) * half_h)
            )
        os.remove(frame)
    tile.save(out)
    return out


def write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)

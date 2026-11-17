"""
Build an LSHE video channel for one grade band and upload it to Kolibri Studio.

    python lshe_video_chef.py dryrun manifest=<manifest.csv> videos=<folder> band=G4-5
    python lshe_video_chef.py --token=<token file> --thumbnails \\
        manifest=<manifest.csv> videos=<folder> band=G4-5

A ricecooker "sushi chef". Run it in the lshe-content-tools Docker image
(see README.md). The manifest comes from make_manifest.py, with band,
license and copyright_holder filled in. Only the rows for the given band go
into the channel, so each band is its own channel, as LSHE grade bands work
per channel. Rows are ordered by code, optionally in topic folders.

"dryrun" builds and checks the channel without uploading. Without it, the
channel is uploaded to Studio as a draft; publish it in Studio after
checking it. Re-running updates the same channel (same source ids), so
fixing a title or adding videos never creates a duplicate.

Options (key=value after the command):

    manifest=   the manifest CSV (required)
    videos=     folder the manifest's file paths are relative to (required)
    band=       grade band to build, e.g. G4-5 (required)
    title=      channel title (default "LSHE <band> videos")
    description= channel description
    channel_thumbnail= optional image for the channel (default: tiled
                from its videos)
    topics=     optional topics CSV: a description (and optionally a
                thumbnail image) for each topic folder. Columns: topic,
                band (empty = every band), description, thumbnail (a path
                relative to the CSV). See topics.csv.example.

Thumbnails: each topic folder, and the channel, gets a thumbnail tiled from
frames of up to four of its videos, unless topics.csv (or channel_thumbnail=)
gives one. Pass
--thumbnails as well, for the videos' own thumbnails.

Videos should already be converted (convert_videos.sh). Don't pass
--compress: it would convert them again at lower quality.
"""
import collections
import csv
import hashlib
import os
import re
import subprocess
import sys

from le_utils.constants import licenses
from ricecooker.chefs import SushiChef
from ricecooker.classes.files import VideoFile
from ricecooker.classes.licenses import get_license
from ricecooker.classes.nodes import TopicNode
from ricecooker.classes.nodes import VideoNode

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


class ManifestError(Exception):
    pass


def license_for(row):
    name = re.sub(r"\s+", " ", (row.get("license") or "").strip().lower())
    name = name.replace("cc-", "cc ")
    # A version is fine ("CC BY-SA 4.0"): Studio records the licence type only.
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
    return get_license(license_id, copyright_holder=holder or None)


def read_manifest(path, videos, band):
    """The manifest rows for band, checked, ordered by code then title."""
    if not os.path.isfile(path):
        raise ManifestError("manifest not found: {}".format(path))
    with open(path, newline="", encoding="utf-8") as f:
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
        key = (row.get("code") or "").strip() or row["file"]
        if key in seen:
            problems.append("{}: listed twice".format(key))
        seen.add(key)
        if not os.path.isfile(os.path.join(videos, row["file"])):
            problems.append("{}: file not found: {}".format(key, row["file"]))
        if not (row.get("title") or "").strip():
            problems.append("{}: title is empty".format(key))
        try:
            license_for(row)
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


def video_node(row, videos):
    key = (row.get("code") or "").strip() or row["file"]
    return VideoNode(
        source_id=key,
        title=row["title"].strip(),
        description=(row.get("description") or "").strip(),
        license=license_for(row),
        language=(row.get("language") or "en").strip(),
        files=[VideoFile(path=os.path.join(videos, row["file"]))],
    )


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


THUMB_W, THUMB_H = 640, 360  # 16:9, as Kolibri shows thumbnails


def video_frame(video, out):
    """Save a frame from a quarter of the way into video, at THUMB_W x THUMB_H."""
    duration = float(
        subprocess.check_output(
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
        or 0
    )
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


class LSHEVideoChef(SushiChef):
    def get_channel(self, **kwargs):
        band = kwargs.get("band") or ""
        if not BAND_RE.match(band):
            sys.exit("give band=<band>, e.g. band=G4-5")
        return self.channel_node_class(
            source_domain=SOURCE_DOMAIN,
            source_id="lshe-videos-{}".format(band.lower()),
            title=kwargs.get("title") or "LSHE {} videos".format(band),
            description=kwargs.get("description")
            or "Life Skills and Health Education videos for grade band {}.".format(
                band
            ),
            language=kwargs.get("language") or "en",
            thumbnail=kwargs.get("thumbnail"),
        )

    def construct_channel(self, **kwargs):
        manifest, videos = kwargs.get("manifest"), kwargs.get("videos")
        if not manifest or not videos:
            sys.exit("give manifest=<manifest.csv> and videos=<folder>")
        band = kwargs.get("band") or ""
        try:
            rows = read_manifest(manifest, videos, band)
            topic_info = read_topics(kwargs.get("topics"), band)
        except ManifestError as e:
            sys.exit(str(e))

        # Group the videos by topic, keeping the manifest's order.
        groups = collections.OrderedDict()
        for row in rows:
            groups.setdefault((row.get("topic") or "").strip(), []).append(row)

        def paths(group):
            return [os.path.join(videos, r["file"]) for r in group]

        thumbs = os.path.join(os.getcwd(), "lshe_thumbnails")
        channel_thumbnail = kwargs.get("channel_thumbnail")
        if channel_thumbnail and not os.path.isfile(channel_thumbnail):
            sys.exit("channel thumbnail not found: {}".format(channel_thumbnail))
        kwargs["thumbnail"] = channel_thumbnail or tiled_thumbnail(paths(rows), thumbs)
        channel = self.get_channel(**kwargs)

        missing = []
        for topic, group in groups.items():
            if not topic:
                for row in group:
                    channel.add_child(video_node(row, videos))
                continue
            info = topic_info.get(topic, {})
            if not info.get("description"):
                missing.append(topic)
            node = TopicNode(
                source_id="topic-" + topic.lower(),
                title=topic,
                description=info.get("description") or "",
                thumbnail=info.get("thumbnail")
                or tiled_thumbnail(paths(group), thumbs),
            )
            channel.add_child(node)
            for row in group:
                node.add_child(video_node(row, videos))
        if missing:
            print("Topics with no description: {}".format(", ".join(missing)))
        print(
            "Channel '{}': {} video(s), {} topic(s).".format(
                channel.title, len(rows), len([t for t in groups if t])
            )
        )
        return channel


if __name__ == "__main__":
    LSHEVideoChef().main()

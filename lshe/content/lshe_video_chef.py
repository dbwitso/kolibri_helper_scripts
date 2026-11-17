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

Videos should already be converted (convert_videos.sh). Don't pass
--compress: it would convert them again at lower quality.
"""
import collections
import csv
import os
import re
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
        )

    def construct_channel(self, **kwargs):
        channel = self.get_channel(**kwargs)
        manifest, videos = kwargs.get("manifest"), kwargs.get("videos")
        if not manifest or not videos:
            sys.exit("give manifest=<manifest.csv> and videos=<folder>")
        try:
            rows = read_manifest(manifest, videos, kwargs["band"])
        except ManifestError as e:
            sys.exit(str(e))
        topics = {}
        for row in rows:
            topic = (row.get("topic") or "").strip()
            parent = channel
            if topic:
                if topic not in topics:
                    topics[topic] = TopicNode(
                        source_id="topic-" + topic.lower(), title=topic
                    )
                    channel.add_child(topics[topic])
                parent = topics[topic]
            parent.add_child(video_node(row, videos))
        print(
            "Channel '{}': {} video(s), {} topic(s).".format(
                channel.title, len(rows), len(topics)
            )
        )
        return channel


if __name__ == "__main__":
    LSHEVideoChef().main()

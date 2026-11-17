"""
Make the thumbnails and measure the videos for one band's offline channel.

    python prepare_media.py --manifest <manifest.csv> --videos <folder> \\
        --band G4-5 --out <media folder> [--topics <topics.csv>] \\
        [--channel-thumbnail <image>]

The first step of building a channel without Kolibri Studio. It runs in the
lshe-content-tools image, which has ffmpeg; build_channel.py then runs in
the Kolibri image, which doesn't. See README.md, "Building channels
offline".

Writes, in <media folder>/<band>/:

    video/<code>.png    a frame from a quarter of the way into each video
    topic/<topic>.png   each topic folder's thumbnail: the one topics.csv
                        names, or frames of up to four of its videos tiled
    channel.png         the channel thumbnail: --channel-thumbnail, or a tile
    media.json          what build_channel.py reads: the video lengths and
                        the thumbnail of each video and topic

Thumbnails already made are kept, so re-running is quick. Delete a
thumbnail to make it again.
"""
import argparse
import os
import shutil
import sys

from channel_common import check_band
from channel_common import group_by_topic
from channel_common import ManifestError
from channel_common import read_manifest
from channel_common import read_topics
from channel_common import row_key
from channel_common import slug
from channel_common import THUMB_H
from channel_common import THUMB_W
from channel_common import tiled_thumbnail
from channel_common import video_duration
from channel_common import video_frame
from channel_common import write_json_atomic


def as_png(src, dest, size=None):
    """Copy an image to dest as PNG, fitted into size (w, h) if given."""
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        if size:
            im.thumbnail(size)
        tmp = dest + ".tmp.png"
        im.save(tmp, "PNG", optimize=True)
    os.replace(tmp, dest)


def prepare_media(manifest, videos, band, out, topics=None, channel_thumbnail=None):
    rows = read_manifest(manifest, videos, band)
    topic_info = read_topics(topics, band)
    folder = os.path.join(out, band)
    tiles = os.path.join(folder, "tiles")
    for sub in ("video", "topic"):
        os.makedirs(os.path.join(folder, sub), exist_ok=True)

    media = {"band": band, "videos": {}, "topics": {}, "channel": "channel.png"}
    for row in rows:
        path = os.path.join(videos, row["file"])
        duration = video_duration(path)
        thumb = os.path.join("video", slug(row_key(row)) + ".png")
        if not os.path.isfile(os.path.join(folder, thumb)):
            print("frame: {}".format(row["file"]))
            video_frame(path, os.path.join(folder, thumb), duration)
        media["videos"][row_key(row)] = {
            "duration": int(round(duration)),
            "thumbnail": thumb,
        }

    groups = group_by_topic(rows)
    for topic, group in groups.items():
        if not topic:
            continue
        thumb = os.path.join("topic", slug(topic) + ".png")
        dest = os.path.join(folder, thumb)
        given = topic_info.get(topic, {}).get("thumbnail")
        if given:
            as_png(given, dest, (THUMB_W, THUMB_H))
        elif not os.path.isfile(dest):
            print("tile: {}".format(topic))
            paths = [os.path.join(videos, r["file"]) for r in group]
            shutil.copyfile(tiled_thumbnail(paths, tiles), dest)
        media["topics"][topic] = thumb

    dest = os.path.join(folder, "channel.png")
    if channel_thumbnail:
        if not os.path.isfile(channel_thumbnail):
            raise ManifestError(
                "channel thumbnail not found: {}".format(channel_thumbnail)
            )
        as_png(channel_thumbnail, dest, (THUMB_W, THUMB_H))
    elif not os.path.isfile(dest):
        paths = [os.path.join(videos, r["file"]) for r in rows]
        shutil.copyfile(tiled_thumbnail(paths, tiles), dest)

    write_json_atomic(os.path.join(folder, "media.json"), media)
    print(
        "{}: {} video(s), {} topic(s) in {}".format(
            band, len(media["videos"]), len(media["topics"]), folder
        )
    )
    return media


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Make thumbnails and measure videos for an offline LSHE channel."
    )
    parser.add_argument("--manifest", required=True, help="Manifest CSV")
    parser.add_argument("--videos", required=True, help="Folder of converted videos")
    parser.add_argument("--band", required=True, help="Grade band, e.g. G4-5")
    parser.add_argument("--out", required=True, help="Media folder to write")
    parser.add_argument("--topics", help="Optional topics CSV")
    parser.add_argument("--channel-thumbnail", help="Optional channel image")
    args = parser.parse_args(argv)
    try:
        prepare_media(
            args.manifest,
            args.videos,
            check_band(args.band),
            args.out,
            args.topics,
            args.channel_thumbnail,
        )
    except ManifestError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()

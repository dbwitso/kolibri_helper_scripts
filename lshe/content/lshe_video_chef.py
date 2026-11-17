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
import os
import sys

from channel_common import BAND_RE
from channel_common import group_by_topic
from channel_common import ManifestError
from channel_common import parse_license
from channel_common import read_manifest
from channel_common import read_topics
from channel_common import row_key
from channel_common import SOURCE_DOMAIN
from channel_common import tiled_thumbnail
from ricecooker.chefs import SushiChef
from ricecooker.classes.files import VideoFile
from ricecooker.classes.licenses import get_license
from ricecooker.classes.nodes import TopicNode
from ricecooker.classes.nodes import VideoNode


def license_for(row):
    license_id, holder = parse_license(row)
    return get_license(license_id, copyright_holder=holder)


def video_node(row, videos):
    return VideoNode(
        source_id=row_key(row),
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

        groups = group_by_topic(rows)

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

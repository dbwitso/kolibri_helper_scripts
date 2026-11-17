"""
Build one band's LSHE video channel offline, without Kolibri Studio.

    python build_channel.py --manifest <manifest.csv> --videos <folder> \\
        --media <media folder> --band G4-5 --out <export folder> \\
        [--topics <topics.csv>] [--title ...] [--description ...] [--version N]

Runs in the LSHE Kolibri image, after prepare_media.py (see README.md,
"Building channels offline"). It writes the same layout as a Kolibri channel
export:

    <export folder>/content/databases/<channel id>.sqlite3
    <export folder>/content/storage/<a>/<b>/<md5>.<ext>

so a centre server imports it with the usual
"import_channels.sh --file channels.conf" (importchannel disk, then
importcontent disk). Both bands can share one export folder.

The channel database is created with Kolibri's own content schema (the
version Kolibri 0.16 exports), so Kolibri reads it like a channel published
on Studio. Nothing is written to any Kolibri facility database.

Ids are fixed: the channel id comes from the band, and each video's ids from
its code. Re-building the channel (to fix a title or add videos) gives the
same ids, so a centre that imports the new version keeps every learner's
progress. Each build raises the channel version by one, which Kolibri needs
to see an update.

The channel ids differ from the Studio channels "LSHE G4-5 videos" and
"LSHE G6-7 videos": the two must never be mixed on one server.
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid

# Start Kolibri in a throwaway home: only its content schema is used.
os.environ["KOLIBRI_HOME"] = tempfile.mkdtemp(prefix="lshe-build-")
for name in list(os.environ):
    if name.startswith("KOLIBRI_DATABASE_"):
        del os.environ[name]

import logging  # noqa E402

logging.disable(logging.WARNING)
try:
    import kolibri  # noqa F401 E402
    import django  # noqa E402

    django.setup()
    from kolibri.core.content.constants.schema_versions import (  # noqa E402
        CONTENT_SCHEMA_VERSION,
    )
    from kolibri.core.content.utils.sqlalchemybridge import Bridge  # noqa E402
    from kolibri.core.fields import create_timezonestamp  # noqa E402
    from kolibri.utils.time_utils import local_now  # noqa E402
finally:
    logging.disable(logging.NOTSET)

from le_utils.constants import content_kinds  # noqa E402
from le_utils.constants import format_presets  # noqa E402
from le_utils.constants import languages  # noqa E402
from le_utils.constants.labels import learning_activities  # noqa E402

from channel_common import check_band  # noqa E402
from channel_common import group_by_topic  # noqa E402
from channel_common import ManifestError  # noqa E402
from channel_common import parse_license  # noqa E402
from channel_common import read_manifest  # noqa E402
from channel_common import read_topics  # noqa E402
from channel_common import row_key  # noqa E402
from channel_common import SOURCE_DOMAIN  # noqa E402

# All ids are derived from this, so they never change between builds.
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, SOURCE_DOMAIN)
RTL = {"ar", "fa", "he", "ps", "ur", "yi", "dv"}
# Kolibri's order of file presets (le_utils): the main file first.
PRIORITY = {p.id: p.order for p in format_presets.PRESETLIST}


def uid(*parts):
    return uuid.uuid5(NAMESPACE, ":".join(parts)).hex


def channel_id_for(band):
    return uid("offline-channel", "lshe-videos-" + band.lower())


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Storage(object):
    """Copies files into <out>/content/storage by checksum, as Kolibri lays them out."""

    def __init__(self, out):
        self.root = os.path.join(out, "content", "storage")
        self.files = {}  # md5 -> (extension, size)

    def add(self, path):
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        checksum = md5_of(path)
        dest = os.path.join(
            self.root, checksum[0], checksum[1], "{}.{}".format(checksum, ext)
        )
        size = os.path.getsize(path)
        if not (os.path.isfile(dest) and os.path.getsize(dest) == size):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".partial"
            shutil.copyfile(path, tmp)
            os.replace(tmp, dest)
        self.files[checksum] = (ext, size)
        return checksum


def language_row(code):
    lang = languages.getlang(code)
    if lang is None:
        raise ManifestError("unknown language code: {}".format(code))
    main, _, sub = code.partition("-")
    return {
        "id": code,
        "lang_code": main,
        "lang_subcode": sub or None,
        "lang_name": lang.native_name or lang.name,
        "lang_direction": "rtl" if main in RTL else "ltr",
    }


def previous_version(db_path):
    if not os.path.isfile(db_path):
        return 0
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT version FROM content_channelmetadata").fetchone()
    finally:
        conn.close()
    return row[0] if row else 0


def build_tree(channel_id, title, description, rows, topic_info, media, videos):
    """The channel's nodes as nested dicts: root -> topics -> videos."""
    language = (rows[0].get("language") or "en").strip()
    root = {
        "id": uid(channel_id, "root"),
        "content_id": uid(channel_id, "root-content"),
        "kind": content_kinds.TOPIC,
        "title": title,
        "description": description,
        "lang_id": language,
        "children": [],
    }

    def video(row):
        key = row_key(row)
        info = media["videos"].get(key)
        if not info:
            raise ManifestError(
                "{}: not in media.json: run prepare_media.py again".format(key)
            )
        license_id, holder = parse_license(row)
        duration = info["duration"]
        return {
            "id": uid(channel_id, "video", key),
            # The same video has the same content id in every channel and
            # build, so learners' progress follows it.
            "content_id": uid("video-content", key),
            "kind": content_kinds.VIDEO,
            "title": row["title"].strip(),
            "description": (row.get("description") or "").strip(),
            "license_name": license_id,
            "license_owner": holder or "",
            "lang_id": (row.get("language") or "en").strip(),
            "duration": duration,
            "learning_activities": learning_activities.WATCH,
            "options": json.dumps(
                {
                    "completion_criteria": {
                        "model": "time",
                        "threshold": duration,
                        "learner_managed": False,
                    }
                }
            ),
            "files": [
                (format_presets.VIDEO_LOW_RES, os.path.join(videos, row["file"])),
                (format_presets.VIDEO_THUMBNAIL, info["thumbnail"]),
            ],
            "children": [],
        }

    for topic, group in group_by_topic(rows).items():
        if not topic:
            root["children"].extend(video(r) for r in group)
            continue
        root["children"].append(
            {
                "id": uid(channel_id, "topic", topic),
                "content_id": uid("topic-content", topic),
                "kind": content_kinds.TOPIC,
                "title": topic,
                "description": topic_info.get(topic, {}).get("description") or "",
                "lang_id": language,
                "files": [(format_presets.TOPIC_THUMBNAIL, media["topics"][topic])],
                "children": [video(r) for r in group],
            }
        )
    return root


def flatten(node, parent_id=None, level=0, counter=None, sort_order=1):
    """Yield (node, row) with MPTT numbers, depth first."""
    counter = counter if counter is not None else [1]
    lft = counter[0]
    counter[0] += 1
    children = []
    for i, child in enumerate(node["children"], 1):
        children.extend(flatten(child, node["id"], level + 1, counter, i))
    row = {
        "id": node["id"],
        "content_id": node["content_id"],
        "kind": node["kind"],
        "title": node["title"][:200],
        "description": node.get("description") or "",
        "license_name": node.get("license_name"),
        "license_description": None,
        "license_owner": node.get("license_owner", ""),
        "author": "",
        "coach_content": False,
        "available": False,
        "sort_order": float(sort_order),
        "options": node.get("options", "{}"),
        "learning_activities": node.get("learning_activities"),
        "duration": node.get("duration"),
        "lang_id": node.get("lang_id"),
        "parent_id": parent_id,
        "lft": lft,
        "rght": counter[0],
        "tree_id": 1,
        "level": level,
    }
    counter[0] += 1
    return [(node, row)] + children


def build_channel(
    manifest,
    videos,
    media_dir,
    band,
    out,
    topics=None,
    title=None,
    description=None,
    version=None,
):
    rows = read_manifest(manifest, videos, band)
    topic_info = read_topics(topics, band)
    media_folder = os.path.join(media_dir, band)
    media_json = os.path.join(media_folder, "media.json")
    if not os.path.isfile(media_json):
        raise ManifestError(
            "{} not found: run prepare_media.py first".format(media_json)
        )
    with open(media_json, encoding="utf-8") as f:
        media = json.load(f)

    channel_id = channel_id_for(band)
    title = title or "LSHE {} videos".format(band)
    description = (
        description
        or "Life Skills and Health Education videos for grade band {}.".format(band)
    )[:400]
    db_dir = os.path.join(out, "content", "databases")
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, "{}.sqlite3".format(channel_id))
    old = previous_version(db_path)
    version = version or old + 1
    if version <= old:
        raise ManifestError(
            "version {} is not above the existing build's {}: a centre would "
            "not see it as an update".format(version, old)
        )

    tree = build_tree(channel_id, title, description, rows, topic_info, media, videos)
    storage = Storage(out)
    node_rows, file_rows = [], []
    langs = set()
    for node, row in flatten(tree):
        row["channel_id"] = channel_id
        node_rows.append(row)
        if row["lang_id"]:
            langs.add(row["lang_id"])
        for preset, path in node.get("files", []):
            if not os.path.isabs(path):
                path = os.path.join(media_folder, path)
            if not os.path.isfile(path):
                raise ManifestError("file not found: {}".format(path))
            checksum = storage.add(path)
            is_thumb = preset in (
                format_presets.VIDEO_THUMBNAIL,
                format_presets.TOPIC_THUMBNAIL,
            )
            file_rows.append(
                {
                    "id": uid(node["id"], "file", preset),
                    "preset": preset,
                    "available": False,
                    "supplementary": is_thumb,
                    "thumbnail": is_thumb,
                    "priority": PRIORITY.get(preset),
                    "contentnode_id": node["id"],
                    "lang_id": None if is_thumb else row["lang_id"],
                    "local_file_id": checksum,
                }
            )

    with open(os.path.join(media_folder, media["channel"]), "rb") as f:
        thumbnail = "data:image/png;base64," + base64.b64encode(f.read()).decode()

    tmp_path = db_path + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    bridge = Bridge(sqlite_file_path=tmp_path, schema_version=CONTENT_SCHEMA_VERSION)
    tables = bridge.Base.metadata.tables
    try:
        with bridge.engine.begin() as conn:
            conn.execute(
                tables["content_language"].insert(),
                [language_row(code) for code in sorted(langs)],
            )
            conn.execute(tables["content_contentnode"].insert(), node_rows)
            conn.execute(
                tables["content_localfile"].insert(),
                [
                    {"id": c, "extension": ext, "available": False, "file_size": size}
                    for c, (ext, size) in sorted(storage.files.items())
                ],
            )
            conn.execute(tables["content_file"].insert(), file_rows)
            conn.execute(
                tables["content_channelmetadata"].insert(),
                {
                    "id": channel_id,
                    "name": title[:200],
                    "description": description,
                    "tagline": None,
                    "author": "",
                    "version": version,
                    "thumbnail": thumbnail,
                    "last_updated": create_timezonestamp(local_now()),
                    "min_schema_version": CONTENT_SCHEMA_VERSION,
                    "root_id": tree["id"],
                },
            )
    finally:
        bridge.end()
    os.replace(tmp_path, db_path)

    n_videos = sum(1 for r in node_rows if r["kind"] == content_kinds.VIDEO)
    n_topics = sum(1 for r in node_rows if r["kind"] == content_kinds.TOPIC) - 1
    size = sum(s for _, s in storage.files.values())
    print(
        "{}  # {} (band {}), version {}: {} video(s), {} topic(s), {:.0f} MB".format(
            channel_id, title, band, version, n_videos, n_topics, size / 1e6
        )
    )
    return channel_id, version


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build an LSHE video channel offline, as a Kolibri channel export."
    )
    parser.add_argument("--manifest", required=True, help="Manifest CSV")
    parser.add_argument("--videos", required=True, help="Folder of converted videos")
    parser.add_argument(
        "--media", required=True, help="Media folder from prepare_media.py"
    )
    parser.add_argument("--band", required=True, help="Grade band, e.g. G4-5")
    parser.add_argument("--out", required=True, help="Export folder to write")
    parser.add_argument("--topics", help="Optional topics CSV (descriptions)")
    parser.add_argument("--title", help='Channel title (default "LSHE <band> videos")')
    parser.add_argument("--description", help="Channel description")
    parser.add_argument(
        "--version",
        type=int,
        help="Channel version (default: one above the existing build, or 1)",
    )
    args = parser.parse_args(argv)
    try:
        build_channel(
            args.manifest,
            args.videos,
            args.media,
            check_band(args.band),
            args.out,
            args.topics,
            args.title,
            args.description,
            args.version,
        )
    except ManifestError as e:
        sys.exit(str(e))
    finally:
        shutil.rmtree(os.environ["KOLIBRI_HOME"], ignore_errors=True)


if __name__ == "__main__":
    main()

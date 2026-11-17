"""
List a folder of LSHE videos in a manifest CSV for lshe_video_chef.py.

    python make_manifest.py <video folder> <manifest.csv>

Each video gets one row, with its code and title read from the file name
("AMZ-017 - Consent.mp4" -> code AMZ-017, title "Consent"). Fill in the
columns left empty before uploading:

    band              G4-5 or G6-7, from the list UNESCO and the Ministry
                      approved. Rows with no band are not uploaded.
    topic             optional folder in the channel, e.g. "Relationships"
    license           one of: CC BY, CC BY-SA, CC BY-ND, CC BY-NC,
                      CC BY-NC-SA, CC BY-NC-ND, All Rights Reserved,
                      Special Permissions, Public Domain
    copyright_holder  who owns the video, e.g. the producer
    description       optional

An existing manifest is never overwritten: new videos are added at the end,
and the rows already there (and anything filled in) are kept.
"""
import argparse
import csv
import os
import re
import sys

COLUMNS = (
    "code",
    "title",
    "file",
    "band",
    "topic",
    "license",
    "copyright_holder",
    "language",
    "description",
)
VIDEO_EXTENSIONS = (".mp4", ".webm", ".m4v", ".mov", ".mkv")
NAME_RE = re.compile(r"^(?P<code>[A-Za-z]+-\d+)\s*-\s*(?P<title>.+)$")
# File names can't hold some characters, so "You're" was saved as "You_re".
APOSTROPHE_RE = re.compile(r"(?<=[A-Za-z])_(?=(s|t|re|ve|ll|d|m)\b)")


def title_and_code(filename):
    stem = os.path.splitext(os.path.basename(filename))[0]
    match = NAME_RE.match(stem)
    code, title = (match.group("code"), match.group("title")) if match else ("", stem)
    title = APOSTROPHE_RE.sub("'", title)
    title = re.sub(r"\s+", " ", title.replace("_", " ")).strip()
    return code.upper(), title


def find_videos(folder):
    found = []
    for root, _, names in os.walk(folder):
        for name in names:
            if name.lower().endswith(VIDEO_EXTENSIONS):
                found.append(os.path.relpath(os.path.join(root, name), folder))
    return sorted(found)


def make_manifest(folder, manifest, language="en"):
    rows = []
    if os.path.exists(manifest):
        with open(manifest, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    known = {row["file"] for row in rows}
    added = 0
    for rel in find_videos(folder):
        if rel in known:
            continue
        code, title = title_and_code(rel)
        row = dict.fromkeys(COLUMNS, "")
        row.update(code=code, title=title, file=rel, language=language)
        rows.append(row)
        added += 1
    tmp = manifest + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, manifest)
    return added, len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="List a folder of LSHE videos in a manifest CSV."
    )
    parser.add_argument("folder", help="Folder of converted videos")
    parser.add_argument("manifest", help="Manifest CSV to create or extend")
    parser.add_argument("--language", default="en", help="Language code (default en)")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.folder):
        sys.exit("not a folder: {}".format(args.folder))
    added, total = make_manifest(args.folder, args.manifest, args.language)
    print("Added {} video(s); {} in {}.".format(added, total, args.manifest))
    print("Fill in band, license and copyright_holder before uploading.")


if __name__ == "__main__":
    main()

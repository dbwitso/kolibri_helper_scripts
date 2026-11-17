#!/usr/bin/env bash

if [ "$1" = "-h" ] || [ "$1" = "--help" ] || [ $# -lt 2 ]; then
  echo "NAME"
  echo "  build_offline_channels - Build LSHE video channels without Kolibri Studio"
  echo
  echo "SYNOPSIS"
  echo "  ./build_offline_channels.sh <work folder> <band> [<band> ...]"
  echo
  echo "DESCRIPTION"
  echo "  Builds one Kolibri channel per grade band from the converted videos and"
  echo "  the manifest, entirely on this workstation, and writes them as a channel"
  echo "  export that a centre server imports with import_channels.sh. No internet"
  echo "  and no Kolibri Studio account are needed."
  echo
  echo "  Uses the work folder layout from README.md:"
  echo "    manifest.csv            the filled-in manifest (required)"
  echo "    h264_480p/              the converted videos (required)"
  echo "    topics.csv              topic descriptions (used if present)"
  echo "    channel_thumbnail.png   the channel's image (used if present)"
  echo "  and writes:"
  echo "    offline/media/<band>/   thumbnails and video lengths (prepare_media.py)"
  echo "    offline/export/         the channel export: content/databases and"
  echo "                            content/storage (build_channel.py)"
  echo "    offline/export/channels.conf   the channel ids, for import_channels.sh"
  echo
  echo "  Step one runs in the lshe-content-tools image (ffmpeg), step two in the"
  echo "  LSHE Kolibri image (Kolibri's own channel schema). Each build raises the"
  echo "  channel version by one and keeps every id, so a centre that imports the"
  echo "  new version keeps learners' progress."
  echo
  echo "  Settings (environment): LSHE_CONTENT_IMAGE (lshe-content-tools),"
  echo "  LSHE_KOLIBRI_IMAGE (lshe-kolibri:latest)."
  echo
  echo "Examples"
  echo "  ./build_offline_channels.sh <work folder> G4-5 G6-7"
  echo "	Builds both LSHE video channels into <work folder>/offline/export"
  exit 1
fi

set -euo pipefail

TOOLS_IMAGE="${LSHE_CONTENT_IMAGE:-lshe-content-tools}"
KOLIBRI_IMAGE="${LSHE_KOLIBRI_IMAGE:-lshe-kolibri:latest}"
CODE="$(cd "$(dirname "$0")" && pwd)"
W="$(cd "$1" && pwd)"
shift

for need in manifest.csv h264_480p; do
  if [[ ! -e "$W/$need" ]]; then
    echo "$W/$need not found: see README.md" >&2
    exit 1
  fi
done
for image in "$TOOLS_IMAGE" "$KOLIBRI_IMAGE"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "Docker image $image not found (see README.md)" >&2
    exit 1
  fi
done

TOPICS=() CHANNEL_THUMB=()
[[ -f "$W/topics.csv" ]] && TOPICS=(--topics /w/topics.csv)
[[ -f "$W/channel_thumbnail.png" ]] && CHANNEL_THUMB=(--channel-thumbnail /w/channel_thumbnail.png)

# Both images run as the current user, so everything written is theirs.
run() {
  local image="$1"
  shift
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$CODE":/code:ro -v "$W":/w -w /code --entrypoint python "$image" "$@"
}

mkdir -p "$W/offline/media" "$W/offline/export"
LINES=()
for band in "$@"; do
  echo "== $band: thumbnails and video lengths"
  run "$TOOLS_IMAGE" /code/prepare_media.py --manifest /w/manifest.csv \
    --videos /w/h264_480p --band "$band" --out /w/offline/media \
    "${TOPICS[@]}" "${CHANNEL_THUMB[@]}"
  echo "== $band: channel"
  line="$(run "$KOLIBRI_IMAGE" /code/build_channel.py --manifest /w/manifest.csv \
    --videos /w/h264_480p --media /w/offline/media --band "$band" \
    --out /w/offline/export "${TOPICS[@]}" | tee /dev/stderr | tail -1)"
  LINES+=("$line")
done

CONF="$W/offline/export/channels.conf"
{
  echo "# LSHE video channels built offline by build_offline_channels.sh."
  echo "# Import with: import_channels.sh --file channels.conf"
  echo "# Then put each channel in its band with set_band_channels.py."
  printf '%s\n' "${LINES[@]}"
} >"$CONF.tmp"
# Keep the lines for bands not rebuilt this time.
if [[ -f "$CONF" ]]; then
  while IFS= read -r old; do
    id="${old%% *}"
    [[ "$old" == \#* || -z "$id" ]] && continue
    grep -q "^$id " "$CONF.tmp" || echo "$old" >>"$CONF.tmp"
  done <"$CONF"
fi
mv "$CONF.tmp" "$CONF"

echo
echo "Done. Export: $W/offline/export ($(du -sh "$W/offline/export" | cut -f1))"
echo "Channel ids: $CONF"

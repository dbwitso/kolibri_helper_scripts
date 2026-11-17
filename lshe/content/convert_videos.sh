#!/usr/bin/env bash

if [ "$1" = "-h" ] || [ "$1" = "--help" ] || [ $# -ne 2 ]; then
  echo "NAME"
  echo "  convert_videos - Convert LSHE videos to H.264 480p for tablets"
  echo
  echo "SYNOPSIS"
  echo "  ./convert_videos.sh <input folder> <output folder>"
  echo
  echo "DESCRIPTION"
  echo "  Converts every .mp4, .mov, .mkv, .webm and .m4v in the input folder to"
  echo "  H.264 (Main profile) video at up to 480 lines with AAC stereo audio, in an"
  echo "  .mp4 that starts playing before it has fully downloaded (faststart)."
  echo "  Every tablet browser plays this, and the files stay small enough for a"
  echo "  whole class streaming over one access point. AV1 and other formats that"
  echo "  low-cost tablets can't decode are converted too."
  echo
  echo "  Runs on a workstation, not the centre server. ffmpeg runs in the"
  echo "  lshe-content-tools Docker image (docker build -t lshe-content-tools .)."
  echo "  The originals are never changed. Videos already converted are skipped,"
  echo "  so an interrupted run can simply be started again. Each output is"
  echo "  checked with ffprobe."
  echo
  echo "  Settings (environment): LSHE_VIDEO_HEIGHT (480), LSHE_VIDEO_CRF (26,"
  echo "  lower is better quality and bigger), LSHE_AUDIO_BITRATE (96k),"
  echo "  LSHE_FFMPEG_THREADS (0 = all cores)."
  echo
  echo "Examples"
  echo "  ./convert_videos.sh <work folder>/original <work folder>/h264_480p"
  echo "	Converts every video in original/ (and its subfolders) into h264_480p/"
  exit 1
fi

set -euo pipefail

IMAGE="${LSHE_CONTENT_IMAGE:-lshe-content-tools}"
HEIGHT="${LSHE_VIDEO_HEIGHT:-480}"
CRF="${LSHE_VIDEO_CRF:-26}"
AUDIO="${LSHE_AUDIO_BITRATE:-96k}"
THREADS="${LSHE_FFMPEG_THREADS:-0}"

IN="$(cd "$1" && pwd)"
mkdir -p "$2"
OUT="$(cd "$2" && pwd)"
if [[ "$IN" == "$OUT" ]]; then
  echo "input and output folders must differ" >&2
  exit 2
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Docker image $IMAGE not found: run 'docker build -t $IMAGE .' in lshe/content" >&2
  exit 1
fi

# ffmpeg runs as the current user, so the outputs are owned by them. The
# input folder is mounted read-only.
run() {
  docker run --rm --user "$(id -u):$(id -g)" \
    -v "$IN":/in:ro -v "$OUT":/out "$IMAGE" "$@"
}

mapfile -d '' FILES < <(cd "$IN" && find . -type f \( -iname '*.mp4' -o -iname '*.mov' \
  -o -iname '*.mkv' -o -iname '*.webm' -o -iname '*.m4v' \) -print0 | sort -z)
if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "no videos in $IN" >&2
  exit 1
fi

echo "Converting ${#FILES[@]} video(s) from $IN to $OUT (H.264 ${HEIGHT}p, CRF $CRF)"
done_n=0 skipped=0 failed=()
for rel in "${FILES[@]}"; do
  rel="${rel#./}"
  dest="${rel%.*}.mp4"
  if [[ -s "$OUT/$dest" ]]; then
    skipped=$((skipped + 1))
    continue
  fi
  mkdir -p "$OUT/$(dirname "$dest")"
  echo "-> $rel"
  # Scale down to HEIGHT lines only if taller; -2 keeps the width even.
  if run ffmpeg -hide_banner -loglevel error -nostdin -y -i "/in/$rel" \
      -map 0:v:0 -map 0:a:0? \
      -c:v libx264 -preset medium -crf "$CRF" -profile:v main -level 3.1 \
      -pix_fmt yuv420p -vf "scale=-2:'min($HEIGHT,ih)'" -threads "$THREADS" \
      -c:a aac -b:a "$AUDIO" -ac 2 \
      -movflags +faststart -f mp4 "/out/$dest.partial" &&
    run ffprobe -v error -select_streams v:0 -show_entries stream=codec_name \
      -of default=nw=1:nk=1 "/out/$dest.partial" | grep -qx h264; then
    mv "$OUT/$dest.partial" "$OUT/$dest"
    done_n=$((done_n + 1))
  else
    rm -f "$OUT/$dest.partial"
    failed+=("$rel")
    echo "   FAILED: $rel" >&2
  fi
done

echo "Converted $done_n, already done $skipped, failed ${#failed[@]}."
echo "Size: $(du -sh "$IN" | cut -f1) before, $(du -sh "$OUT" | cut -f1) after."
if [[ ${#failed[@]} -gt 0 ]]; then
  printf '  failed: %s\n' "${failed[@]}" >&2
  exit 1
fi

#!/usr/bin/env bash

if [ "$1" = "-h" ] || [ "$1" = "--help" ] || [ $# -eq 0 ]; then
  echo "NAME"
  echo "  import_channels - Import LSHE and teacher channels into the Kolibri container"
  echo
  echo "SYNOPSIS"
  echo "  ./import_channels.sh [--network <url>] <channel_id> [<channel_id> ...]"
  echo "  ./import_channels.sh [--network <url>] --file <channels file>"
  echo
  echo "DESCRIPTION"
  echo "  Runs on the LSHE centre server (the host). For each channel, imports the"
  echo "  channel and then all its content (importchannel, then importcontent)"
  echo "  inside the Kolibri container. Re-running is safe: content already on the"
  echo "  server is skipped."
  echo
  echo "  By default the channels come from the channel packs in /srv/lshe-content"
  echo "  on the host (LSHE_CONTENT_DIR), which the container sees read-only at"
  echo "  /srv/lshe-content. With --network, they come from another Kolibri server"
  echo "  on the network instead, e.g. --network http://192.168.1.20:8080"
  echo
  echo "  A channels file has one channel id per line; anything after # is a"
  echo "  comment (see channels.conf.example)."
  echo
  echo "  Afterwards: put the teacher channel ids in LSHE_TEACHER_CHANNEL_IDS in"
  echo "  docker/lshe/.env, and set the grade bands (set_band_channels.py)."
  echo
  echo "Examples"
  echo "  ./import_channels.sh --file channels.conf"
  echo "	Imports every channel listed in channels.conf from /srv/lshe-content"
  echo
  echo "  ./import_channels.sh 0123456789abcdef0123456789abcdef"
  echo "	Imports one channel from /srv/lshe-content"
  echo
  echo "  ./import_channels.sh --network http://192.168.1.20:8080 --file channels.conf"
  echo "	Imports the channels from another Kolibri server"
  exit 1
fi

set -euo pipefail

KOLIBRI="${LSHE_KOLIBRI_CONTAINER:-lshe-kolibri}"
# The container's view of LSHE_CONTENT_DIR (see docker-compose.yml).
CONTENT_DIR="${LSHE_CONTENT_MOUNT:-/srv/lshe-content}"
LOG_DIR="${LSHE_LOG_DIR:-/var/log/lshe}"
LOG_FILE="${LOG_DIR}/channels.log"

BASEURL=""
CHANNELS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --network)
      BASEURL="${2:?--network needs a URL}"
      shift 2
      ;;
    --file)
      FILE="${2:?--file needs a path}"
      if [[ ! -r "$FILE" ]]; then
        echo "cannot read $FILE" >&2
        exit 2
      fi
      while IFS= read -r line || [[ -n "$line" ]]; do
        id="${line%%#*}"
        id="${id//[[:space:]]/}"
        [[ -n "$id" ]] && CHANNELS+=("$id")
      done <"$FILE"
      shift 2
      ;;
    *)
      CHANNELS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#CHANNELS[@]} -eq 0 ]]; then
  echo "no channel ids given" >&2
  exit 2
fi
for id in "${CHANNELS[@]}"; do
  if ! [[ "${id//-/}" =~ ^[0-9a-fA-F]{32}$ ]]; then
    echo "not a channel id: $id" >&2
    exit 2
  fi
done

mkdir -p "$LOG_DIR"
log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" | tee -a "$LOG_FILE"; }

if ! docker inspect -f '{{.State.Running}}' "$KOLIBRI" 2>/dev/null | grep -q true; then
  log "ERROR container $KOLIBRI is not running"
  exit 1
fi

if [[ -n "$BASEURL" ]]; then
  SOURCE=(network)
  WHERE=(--baseurl "$BASEURL")
  log "importing ${#CHANNELS[@]} channel(s) from $BASEURL"
else
  if ! docker exec "$KOLIBRI" test -d "$CONTENT_DIR/content"; then
    log "ERROR no channel packs in $CONTENT_DIR inside $KOLIBRI (expected $CONTENT_DIR/content): copy them to LSHE_CONTENT_DIR on the host (default /srv/lshe-content)"
    exit 1
  fi
  SOURCE=(disk)
  WHERE=("$CONTENT_DIR")
  log "importing ${#CHANNELS[@]} channel(s) from $CONTENT_DIR"
fi

manage() { docker exec "$KOLIBRI" kolibri manage "$@"; }

FAILED=()
for id in "${CHANNELS[@]}"; do
  log "channel $id: importchannel"
  if ! manage importchannel "${SOURCE[@]}" "$id" "${WHERE[@]}" 2>&1 | tee -a "$LOG_FILE"; then
    log "ERROR channel $id: importchannel failed"
    FAILED+=("$id")
    continue
  fi
  log "channel $id: importcontent"
  if ! manage importcontent "${SOURCE[@]}" "$id" "${WHERE[@]}" 2>&1 | tee -a "$LOG_FILE"; then
    log "ERROR channel $id: importcontent failed"
    FAILED+=("$id")
    continue
  fi
  log "channel $id: done"
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  log "ERROR ${#FAILED[@]} channel(s) failed: ${FAILED[*]}"
  exit 1
fi
log "all ${#CHANNELS[@]} channel(s) imported"

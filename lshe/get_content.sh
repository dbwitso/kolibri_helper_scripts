#!/usr/bin/env bash

usage() {
  echo "NAME"
  echo "  get_content - Copy a channel pack to this LSHE server, check it, and import it"
  echo
  echo "SYNOPSIS"
  echo "  ./get_content.sh --find"
  echo "  ./get_content.sh --from <pack folder> [--import] [--quick]"
  echo "  ./get_content.sh --from-ssh <user@host:/path/to/pack> [--port <n>] [--key <file>] [--import] [--quick]"
  echo
  echo "DESCRIPTION"
  echo "  Runs on the LSHE centre server (the host), as root. Copies a channel pack"
  echo "  (a folder with content/databases, content/storage and channels.conf, as"
  echo "  export_channels.sh or build_offline_channels.sh write it) into"
  echo "  \$LSHE_CONTENT_DIR (/srv/lshe-content), then checks it: every channel's"
  echo "  database is there and every file matches its checksum (Kolibri names"
  echo "  files by their MD5). With --import, it then runs import_channels.sh for"
  echo "  the pack's channels."
  echo
  echo "  --find        list the channel packs on plugged-in drives (/media, /mnt,"
  echo "                /run/media), with their channels and size"
  echo "  --from        a pack on this server, e.g. a USB drive's folder"
  echo "  --from-ssh    a pack on another machine, copied over SSH (rsync); asks"
  echo "                for the password unless --key is given"
  echo "  --port, --key SSH port (default 22) and private key file"
  echo "  --import      import the channels into Kolibri afterwards"
  echo "  --quick       check that files are there, not their checksums (faster)"
  echo
  echo "  Copying resumes where it stopped if interrupted (run it again). Packs"
  echo "  are merged: files already copied are skipped, and the pack's channel"
  echo "  ids are added to \$LSHE_CONTENT_DIR/channels.conf. Free space is checked"
  echo "  first. Logged to \$LSHE_LOG_DIR/content.log (/var/log/lshe)."
  echo
  echo "Examples"
  echo "  ./get_content.sh --find"
  echo "	Shows the packs on the USB drive just plugged in"
  echo
  echo "  ./get_content.sh --from /media/usb/lshe-pack --import"
  echo "	Copies, checks and imports a pack from a USB drive"
  echo
  echo "  ./get_content.sh --from-ssh root@100.85.89.196:/srv/lshe-export/lshe-pack --import"
  echo "	Copies a pack from another server over SSH (e.g. over NetBird)"
}

if [[ $# -eq 0 ]] || [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]]; then
  usage
  exit 1
fi

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=content_lib.sh
source "$HERE/content_lib.sh"

DEST="${LSHE_CONTENT_DIR:-/srv/lshe-content}"
LOG_DIR="${LSHE_LOG_DIR:-/var/log/lshe}"
LOG_FILE="${LOG_DIR}/content.log"
# Space to leave free beyond the pack, in MB.
MARGIN_MB="${LSHE_CONTENT_MARGIN_MB:-2048}"
SSH="${LSHE_SSH:-ssh}"

MODE=""
SRC=""
PORT=22
KEY=""
IMPORT=0
CHECK=repair
while [[ $# -gt 0 ]]; do
  case "$1" in
    --find) MODE=find; shift ;;
    --from) MODE=disk; SRC="${2:?--from needs a folder}"; shift 2 ;;
    --from-ssh) MODE=ssh; SRC="${2:?--from-ssh needs user@host:/path}"; shift 2 ;;
    --port) PORT="${2:?--port needs a number}"; shift 2 ;;
    --key) KEY="${2:?--key needs a file}"; shift 2 ;;
    --import) IMPORT=1; shift ;;
    --quick) CHECK=quick; shift ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

mkdir -p "$LOG_DIR"
log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" | tee -a "$LOG_FILE"; }

if [[ "$MODE" == "find" ]]; then
  found=0
  while IFS= read -r db; do
    pack="$(dirname "$(dirname "$db")")"
    found=$((found + 1))
    echo "$pack"
    echo "  channels: $(pack_channel_ids "$pack" | tr '\n' ' ')"
    echo "  size: $(pack_size_mb "$pack") MB"
  done < <(find /media /mnt /run/media -maxdepth 5 -type d -path '*/content/databases' 2>/dev/null)
  [[ "$found" -gt 0 ]] || echo "No channel packs found on plugged-in drives (looked in /media, /mnt, /run/media). Is the drive mounted? Try: lsblk"
  exit 0
fi

mkdir -p "$DEST"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

if [[ "$MODE" == "disk" ]]; then
  SRC="${SRC%/}"
  log "pack from $SRC"
  if ! verify_pack "$SRC" quick >/dev/null; then
    log "ERROR $SRC is not a complete channel pack:"
    verify_pack "$SRC" quick | tee -a "$LOG_FILE"
    exit 1
  fi
  need_mb="$(pack_size_mb "$SRC")"
  [[ -r "$SRC/channels.conf" ]] && cp "$SRC/channels.conf" "$STAGE/channels.conf"
  copy() { rsync -a --partial --human-readable --info=progress2 --exclude=channels.conf "$SRC/" "$DEST/"; }
else
  if ! [[ "$SRC" == *:* ]]; then
    echo "--from-ssh needs user@host:/path" >&2
    exit 2
  fi
  HOST="${SRC%%:*}"
  RPATH="${SRC#*:}"
  RPATH="${RPATH%/}"
  SSH_CMD=("$SSH" -p "$PORT" -o ConnectTimeout=20)
  [[ -n "$KEY" ]] && SSH_CMD+=(-i "$KEY")
  log "pack from $HOST:$RPATH (SSH)"
  # One connection to read the pack's size and channels.conf.
  if ! info="$("${SSH_CMD[@]}" "$HOST" "test -d '$RPATH/content/databases' && du -sm '$RPATH/content' | cut -f1 && cat '$RPATH/channels.conf' 2>/dev/null")"; then
    log "ERROR cannot read a channel pack at $HOST:$RPATH (is it a pack, and can this server reach it over SSH?)"
    exit 1
  fi
  need_mb="$(head -1 <<<"$info")"
  tail -n +2 <<<"$info" >"$STAGE/channels.conf"
  [[ -s "$STAGE/channels.conf" ]] || rm -f "$STAGE/channels.conf"
  copy() {
    rsync -a --partial --human-readable --info=progress2 --exclude=channels.conf \
      -e "${SSH_CMD[*]}" "$HOST:$RPATH/" "$DEST/"
  }
fi

free_mb="$(df -Pm "$DEST" | awk 'NR==2 {print $4}')"
log "pack is ${need_mb} MB; ${free_mb} MB free in $DEST"
if [[ "$free_mb" -lt $((need_mb + MARGIN_MB)) ]]; then
  log "ERROR not enough space in $DEST: need ${need_mb} MB plus ${MARGIN_MB} MB to spare"
  exit 1
fi

log "copying to $DEST (run again to resume if interrupted)"
if ! copy 2>&1 | tee -a "$LOG_FILE"; then
  log "ERROR copy failed or was interrupted; run the same command again to resume"
  exit 1
fi

# Add the pack's channels to DEST/channels.conf, keeping those already there.
if [[ -r "$STAGE/channels.conf" ]]; then
  touch "$DEST/channels.conf"
  while IFS= read -r line || [[ -n "$line" ]]; do
    id="${line%%#*}"
    id="${id//[[:space:]]/}"
    [[ -z "$id" ]] && continue
    grep -q "^[[:space:]]*$id" "$DEST/channels.conf" || echo "$line" >>"$DEST/channels.conf"
  done <"$STAGE/channels.conf"
fi

log "checking $DEST ($CHECK)"
if ! verify_pack "$DEST" "$CHECK" | tee -a "$LOG_FILE"; then
  log "ERROR the copied content is not complete; run the same command again to copy what is missing."
  log "      If the same files are damaged again, the pack itself is damaged: make a new one (export_channels.sh)."
  exit 1
fi

if [[ "$IMPORT" -eq 1 ]]; then
  log "importing the channels into Kolibri"
  "$HERE/import_channels.sh" --file "$DEST/channels.conf" || exit 1
else
  log "done. To import: sudo ./import_channels.sh --file $DEST/channels.conf"
fi

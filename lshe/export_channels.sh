#!/usr/bin/env bash

usage() {
  echo "NAME"
  echo "  export_channels - Make a channel pack from this LSHE server's channels"
  echo
  echo "SYNOPSIS"
  echo "  ./export_channels.sh [--to <folder>] [--name <pack name>] --all"
  echo "  ./export_channels.sh [--to <folder>] [--name <pack name>] --file <channels file>"
  echo "  ./export_channels.sh [--to <folder>] [--name <pack name>] <channel_id> [<channel_id> ...]"
  echo
  echo "DESCRIPTION"
  echo "  Runs on an LSHE server that has the channels (the host), as root."
  echo "  Exports the channels and all their content from Kolibri (exportchannel,"
  echo "  exportcontent) as a channel pack in \$LSHE_EXPORT_DIR/<pack name>"
  echo "  (/srv/lshe-export/lshe-pack-<date>), with a channels.conf naming each"
  echo "  channel and its grade band. With --to, the pack is then copied to that"
  echo "  folder (e.g. a USB drive) and every copied file is checked against its"
  echo "  checksum. Another server then copies it in with get_content.sh."
  echo
  echo "  --all     every channel on this server"
  echo "  --file    the channels listed in a channels file (one id per line)"
  echo "  --to      also copy the pack here, e.g. /media/usb (the pack goes in"
  echo "            <folder>/<pack name>)"
  echo "  --name    the pack's folder name (default lshe-pack-<date>)"
  echo
  echo "  Needs the export folder mounted in the Kolibri container"
  echo "  (LSHE_EXPORT_DIR in docker-compose.yml of the Kolibri repository)."
  echo "  Logged to \$LSHE_LOG_DIR/content.log (/var/log/lshe)."
  echo
  echo "Examples"
  echo "  ./export_channels.sh --all --to /media/usb"
  echo "	Puts every channel on a USB drive, ready for another school"
  echo
  echo "  ./export_channels.sh --all"
  echo "	Makes a pack in /srv/lshe-export, for another server to fetch over SSH"
}

if [[ $# -eq 0 ]] || [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]]; then
  usage
  exit 1
fi

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=content_lib.sh
source "$HERE/content_lib.sh"

KOLIBRI="${LSHE_KOLIBRI_CONTAINER:-lshe-kolibri}"
EXPORT_DIR="${LSHE_EXPORT_DIR:-/srv/lshe-export}"
# The same folder inside the Kolibri container (docker-compose.yml).
EXPORT_MOUNT="${LSHE_EXPORT_MOUNT:-/srv/lshe-export}"
LOG_DIR="${LSHE_LOG_DIR:-/var/log/lshe}"
LOG_FILE="${LOG_DIR}/content.log"
MARGIN_MB="${LSHE_CONTENT_MARGIN_MB:-2048}"

TO=""
NAME="lshe-pack-$(date +%Y%m%d)"
ALL=0
CHANNELS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --to) TO="${2:?--to needs a folder}"; shift 2 ;;
    --name) NAME="${2:?--name needs a name}"; shift 2 ;;
    --all) ALL=1; shift ;;
    --file)
      FILE="${2:?--file needs a path}"
      [[ -r "$FILE" ]] || { echo "cannot read $FILE" >&2; exit 2; }
      while IFS= read -r line || [[ -n "$line" ]]; do
        id="${line%%#*}"; id="${id//[[:space:]]/}"
        [[ -n "$id" ]] && CHANNELS+=("$id")
      done <"$FILE"
      shift 2
      ;;
    -*) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    *) CHANNELS+=("$1"); shift ;;
  esac
done

if ! [[ "$NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "--name may hold only letters, digits, . _ and -" >&2
  exit 2
fi

mkdir -p "$LOG_DIR"
log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" | tee -a "$LOG_FILE"; }

if ! docker inspect -f '{{.State.Running}}' "$KOLIBRI" 2>/dev/null | grep -q true; then
  log "ERROR container $KOLIBRI is not running"
  exit 1
fi
if ! docker exec "$KOLIBRI" test -w "$EXPORT_MOUNT"; then
  log "ERROR $EXPORT_MOUNT is not writable in $KOLIBRI: update the Kolibri checkout (its docker-compose.yml mounts LSHE_EXPORT_DIR) and run docker compose up -d"
  exit 1
fi

# id <TAB> name <TAB> band, for every channel on this server.
CATALOGUE="$(docker exec -i "$KOLIBRI" kolibri shell 2>/dev/null <<'PY' | sed -n 's/^CHANNEL\t//p'
from kolibri.core.content.models import ChannelMetadata
from kolibri.plugins.edulution.models import GradeBandChannel
bands = dict(GradeBandChannel.objects.values_list("channel_id", "band"))
for c in ChannelMetadata.objects.order_by("name"):
    print("CHANNEL\t%s\t%s\t%s" % (c.id, c.name.replace("\t", " "), bands.get(c.id, "")))
PY
)"
if [[ "$ALL" -eq 1 ]]; then
  while IFS=$'\t' read -r id _; do
    [[ -n "$id" ]] && CHANNELS+=("$id")
  done <<<"$CATALOGUE"
fi
if [[ ${#CHANNELS[@]} -eq 0 ]]; then
  log "ERROR no channels to export (give --all, --file or channel ids)"
  exit 2
fi
for id in "${CHANNELS[@]}"; do
  if ! grep -q "^${id//-/}"$'\t' <<<"$CATALOGUE"; then
    log "ERROR channel $id is not on this server"
    exit 2
  fi
done

PACK="$EXPORT_DIR/$NAME"
log "exporting ${#CHANNELS[@]} channel(s) to $PACK"
# Kolibri's start-up chatter about network tools (the container has no
# "ip") is not an error; keep it out of the output and log.
manage() {
  docker exec "$KOLIBRI" kolibri manage "$@" 2>&1 | grep -v -e "ip: not found" -e "Neither \`ifconfig\`" \
    -e "being overridden by environment variable" -e "Running Kolibri with the following settings"
  return "${PIPESTATUS[0]}"
}
for id in "${CHANNELS[@]}"; do
  log "channel $id: exportchannel"
  manage exportchannel "$id" "$EXPORT_MOUNT/$NAME" 2>&1 | tee -a "$LOG_FILE" || { log "ERROR exportchannel $id failed"; exit 1; }
  log "channel $id: exportcontent"
  manage exportcontent "$id" "$EXPORT_MOUNT/$NAME" 2>&1 | tee -a "$LOG_FILE" || { log "ERROR exportcontent $id failed"; exit 1; }
done

# Written by the container, which owns the pack folder it created.
if ! {
  echo "# LSHE channel pack $NAME, from $(hostname) on $(date '+%Y-%m-%d')"
  echo "# Import with: get_content.sh --from <this folder> --import"
  for id in "${CHANNELS[@]}"; do
    IFS=$'\t' read -r _ cname band < <(grep "^${id//-/}"$'\t' <<<"$CATALOGUE")
    echo "$id  # $cname${band:+ (band $band)}"
  done
} | docker exec -i "$KOLIBRI" sh -c "cat > '$EXPORT_MOUNT/$NAME/channels.conf'"; then
  log "ERROR could not write $PACK/channels.conf"
  exit 1
fi

log "checking $PACK"
verify_pack "$PACK" quick | tee -a "$LOG_FILE" || { log "ERROR the exported pack is not complete"; exit 1; }

if [[ -n "$TO" ]]; then
  TARGET="${TO%/}/$NAME"
  mkdir -p "$TARGET" || { log "ERROR cannot create $TARGET"; exit 1; }
  need_mb="$(pack_size_mb "$PACK")"
  free_mb="$(df -Pm "$TARGET" | awk 'NR==2 {print $4}')"
  if [[ "$free_mb" -lt $((need_mb + 100)) ]]; then
    log "ERROR not enough space on $TO: the pack is ${need_mb} MB, ${free_mb} MB free"
    exit 1
  fi
  log "copying the pack to $TARGET"
  rsync -rt --partial --human-readable --info=progress2 "$PACK/" "$TARGET/" 2>&1 | tee -a "$LOG_FILE" ||
    { log "ERROR copy to $TARGET failed; run the same command again to resume"; exit 1; }
  sync
  log "checking every file on $TARGET against its checksum"
  verify_pack "$TARGET" full | tee -a "$LOG_FILE" || { log "ERROR the copy on $TARGET is damaged; copy again"; exit 1; }
  log "done: $TARGET is ready. Unmount the drive before unplugging it (umount $TO)."
else
  log "done: pack in $PACK ($(pack_size_mb "$PACK") MB). Another server can fetch it with:"
  log "  get_content.sh --from-ssh root@<this server>:$PACK --import"
fi

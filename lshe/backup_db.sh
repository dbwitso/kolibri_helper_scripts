#!/usr/bin/env bash

if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  echo "NAME"
  echo "  backup_db - Back up the LSHE Kolibri database"
  echo
  echo "SYNOPSIS"
  echo "  ./backup_db.sh"
  echo
  echo "DESCRIPTION"
  echo "  Runs on the LSHE centre server (the host), as root. Dumps the Kolibri"
  echo "  database (PostgreSQL, in the lshe-db container) to"
  echo "  \$LSHE_BACKUP_DIR (/srv/lshe-backups) as kolibri_<date>_nightly.dump,"
  echo "  checks that the dump can be read back, and deletes nightly dumps older"
  echo "  than \$LSHE_BACKUP_KEEP_DAYS (14) days. Dumps taken before a roster"
  echo "  import (run_import.sh) are never deleted."
  echo
  echo "  The systemd timer lshe-backup.timer runs it every night at 01:30"
  echo "  Lusaka time. Output is logged to \$LSHE_LOG_DIR/backup.log"
  echo "  (/var/log/lshe). To restore a dump, see the Kolibri repository's"
  echo "  docs/lshe/deployment.md (Backup and restore)."
  echo
  echo "  The dumps hold learner data: keep them on this server (mode 600, in a"
  echo "  folder only root can open), or copy them only to storage as safe."
  echo
  echo "Examples"
  echo "  ./backup_db.sh"
  echo "	Back up now"
  echo
  echo "  LSHE_BACKUP_KEEP_DAYS=30 ./backup_db.sh"
  echo "	Back up, keeping 30 days of nightly dumps"
  exit 1
fi

set -uo pipefail

DB="${LSHE_DB_CONTAINER:-lshe-db}"
BACKUP_DIR="${LSHE_BACKUP_DIR:-/srv/lshe-backups}"
KEEP_DAYS="${LSHE_BACKUP_KEEP_DAYS:-14}"
LOG_DIR="${LSHE_LOG_DIR:-/var/log/lshe}"
LOG_FILE="${LOG_DIR}/backup.log"
REPORT_TZ="${LSHE_REPORT_TIMEZONE:-Africa/Lusaka}"
# Refuse to start with less free space than this (MB) in the backup folder.
MIN_FREE_MB="${LSHE_BACKUP_MIN_FREE_MB:-1024}"

mkdir -p "$LOG_DIR"

log() {
  printf '%s %s\n' "$(TZ="$REPORT_TZ" date '+%Y-%m-%dT%H:%M:%S%z')" "$*" | tee -a "$LOG_FILE"
}

if ! [[ "$KEEP_DAYS" =~ ^[0-9]+$ ]] || [[ "$KEEP_DAYS" -lt 1 ]]; then
  log "ERROR LSHE_BACKUP_KEEP_DAYS must be a whole number of days, at least 1"
  exit 2
fi

# Only one backup at a time.
exec 9>"${LOG_DIR}/backup.lock"
if ! flock -n 9; then
  log "another backup is running; exiting"
  exit 1
fi

if ! docker inspect -f '{{.State.Running}}' "$DB" 2>/dev/null | grep -q true; then
  log "ERROR container $DB is not running; no backup taken"
  exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

free_mb=$(df -Pm "$BACKUP_DIR" | awk 'NR==2 {print $4}')
if [[ -n "$free_mb" && "$free_mb" -lt "$MIN_FREE_MB" ]]; then
  log "ERROR only ${free_mb} MB free in $BACKUP_DIR (need ${MIN_FREE_MB} MB); no backup taken"
  exit 1
fi

BACKUP="${BACKUP_DIR}/kolibri_$(TZ="$REPORT_TZ" date +%Y%m%d_%H%M%S)_nightly.dump"
PARTIAL="${BACKUP}.partial"
log "backing up the Kolibri database to $BACKUP"

# Written under a temporary name first, so a failed dump never looks like a
# backup, nor replaces the last good one.
umask 077
if ! docker exec "$DB" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$PARTIAL"; then
  log "ERROR pg_dump failed; no backup taken"
  rm -f "$PARTIAL"
  exit 1
fi

# A dump that pg_restore cannot list is not a backup.
if ! docker exec -i "$DB" pg_restore --list >/dev/null <"$PARTIAL"; then
  log "ERROR the dump is not readable; no backup taken"
  rm -f "$PARTIAL"
  exit 1
fi

mv "$PARTIAL" "$BACKUP"
chmod 600 "$BACKUP"
log "backup ok ($(du -h "$BACKUP" | cut -f1))"

# Keep KEEP_DAYS days of nightly dumps; leave every other file alone (the
# dumps taken before a roster import in particular).
removed=0
while IFS= read -r old; do
  rm -f "$old" && removed=$((removed + 1))
done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'kolibri_*_nightly.dump' -mtime +"$((KEEP_DAYS - 1))")
# Leftovers of an interrupted run.
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'kolibri_*_nightly.dump.partial' -mmin +60 -delete

kept=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'kolibri_*_nightly.dump' | wc -l)
log "kept $kept nightly backup(s), removed $removed older than $KEEP_DAYS days"

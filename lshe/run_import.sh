#!/usr/bin/env bash

if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  echo "NAME"
  echo "  run_import - Import learners and classes from MindSpark into Kolibri"
  echo
  echo "SYNOPSIS"
  echo "  ./run_import.sh [--apply] [import options]"
  echo
  echo "DESCRIPTION"
  echo "  Runs on the LSHE centre server (the host), as root. Reads the MindSpark"
  echo "  roster (read-only) and creates or updates the centre's Kolibri learners"
  echo "  and their classes, by running import_from_mindspark.py inside the Kolibri"
  echo "  container."
  echo
  echo "  Without --apply, only a dry run is done: the counts are shown and"
  echo "  nothing is changed. With --apply, a dry run is done first, then the"
  echo "  Kolibri database is backed up (and the backup checked), then the import"
  echo "  runs. Output is logged to \$LSHE_LOG_DIR/import.log (/var/log/lshe)."
  echo
  echo "  Import options are passed to import_from_mindspark.py:"
  echo "    --org-id <ID>       only learners with this MindSpark orgID"
  echo "    --remove-inactive   take MindSpark-deactivated learners out of classes"
  echo "    --facility <ID>     Kolibri facility (default: the default facility)"
  echo "    --staff-pattern <RE> skip usernames matching RE instead of using roles"
  echo
  echo "Examples"
  echo "  ./run_import.sh --org-id ORG1"
  echo "	Dry run only: shows what would change"
  echo
  echo "  ./run_import.sh --apply --org-id ORG1"
  echo "	Dry run, backup, then import"
  echo
  echo "  ./run_import.sh --apply --org-id ORG1 --remove-inactive"
  echo "	End of term: also take learners who left out of their classes"
  exit 1
fi

set -euo pipefail

KOLIBRI="${LSHE_KOLIBRI_CONTAINER:-lshe-kolibri}"
DB="${LSHE_DB_CONTAINER:-lshe-db}"
# Where docker-compose.yml mounts this repository inside the Kolibri container.
SCRIPTS="${LSHE_HELPER_SCRIPTS_MOUNT:-/opt/kolibri_helper_scripts}"
BACKUP_DIR="${LSHE_BACKUP_DIR:-/srv/lshe-backups}"
LOG_DIR="${LSHE_LOG_DIR:-/var/log/lshe}"
LOG_FILE="${LOG_DIR}/import.log"

APPLY=0
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --dryrun) ;;  # always added for the first pass; ignore
    *) ARGS+=("$arg") ;;
  esac
done

mkdir -p "$LOG_DIR"
log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" | tee -a "$LOG_FILE"; }

for c in "$KOLIBRI" "$DB"; do
  if ! docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null | grep -q true; then
    log "ERROR container $c is not running"
    exit 1
  fi
done

IMPORTER="${SCRIPTS}/lshe/import_from_mindspark.py"
if ! docker exec "$KOLIBRI" test -f "$IMPORTER"; then
  log "ERROR $IMPORTER not found in $KOLIBRI: is kolibri_helper_scripts mounted? (LSHE_HELPER_SCRIPTS_DIR in docker/lshe/.env)"
  exit 1
fi

run_importer() { docker exec "$KOLIBRI" python -u "$IMPORTER" "$@"; }

log "dry run: import_from_mindspark.py --dryrun ${ARGS[*]:-}"
if ! run_importer --dryrun ${ARGS[@]+"${ARGS[@]}"} 2>&1 | tee -a "$LOG_FILE"; then
  log "ERROR dry run failed; nothing was changed"
  exit 1
fi

if [[ $APPLY -ne 1 ]]; then
  log "dry run only: nothing was changed. Re-run with --apply to import."
  exit 0
fi

# Back up first. Database credentials come from the db container's own env.
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
BACKUP="${BACKUP_DIR}/kolibri_$(date +%Y%m%d_%H%M%S)_before_import.dump"
log "backing up the Kolibri database to $BACKUP"
if ! docker exec "$DB" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$BACKUP"; then
  log "ERROR backup failed; import not run"
  rm -f "$BACKUP"
  exit 1
fi
# A dump that pg_restore cannot list is not a backup.
if ! docker exec -i "$DB" pg_restore --list >/dev/null <"$BACKUP"; then
  log "ERROR backup $BACKUP is not readable; import not run"
  exit 1
fi
chmod 600 "$BACKUP"
log "backup ok ($(du -h "$BACKUP" | cut -f1))"

log "import: import_from_mindspark.py ${ARGS[*]:-}"
if run_importer ${ARGS[@]+"${ARGS[@]}"} 2>&1 | tee -a "$LOG_FILE"; then
  log "import done. To undo it, restore $BACKUP (see Kolibri docs/lshe/deployment.md, Restore)."
else
  log "ERROR import failed; it runs in one transaction, so nothing was written. Backup: $BACKUP"
  exit 1
fi

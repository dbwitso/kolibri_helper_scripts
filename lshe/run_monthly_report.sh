#!/usr/bin/env bash

if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  echo "NAME"
  echo "  run_monthly_report - Write the LSHE monthly report for this centre"
  echo
  echo "SYNOPSIS"
  echo "  ./run_monthly_report.sh [YYYY-MM]"
  echo
  echo "DESCRIPTION"
  echo "  Runs on the LSHE centre server (the host), as root. Runs lshe_report.py"
  echo "  inside the Kolibri container for the previous month (Africa/Lusaka time),"
  echo "  or for the month given. It writes lshe_<CENTRE_CODE>_<YYYY-MM>.csv and"
  echo "  .json to /srv/lshe-reports: aggregate figures, no personal data."
  echo
  echo "  The systemd timer lshe-report.timer runs it on the 1st of each month at"
  echo "  02:00 Lusaka time. Re-running a month overwrites its file with the same"
  echo "  figures. Output is logged to \$LSHE_LOG_DIR/report.log (/var/log/lshe)."
  echo
  echo "Examples"
  echo "  ./run_monthly_report.sh"
  echo "	Report the previous month"
  echo
  echo "  ./run_monthly_report.sh 2027-03"
  echo "	Report (or re-run) March 2027"
  exit 1
fi

set -euo pipefail

CONTAINER="${LSHE_KOLIBRI_CONTAINER:-lshe-kolibri}"
# Where docker-compose.yml mounts this repository inside the Kolibri container.
SCRIPTS="${LSHE_HELPER_SCRIPTS_MOUNT:-/opt/kolibri_helper_scripts}"
LOG_DIR="${LSHE_LOG_DIR:-/var/log/lshe}"
LOG_FILE="${LOG_DIR}/report.log"
REPORT_TZ="${LSHE_REPORT_TIMEZONE:-Africa/Lusaka}"

mkdir -p "$LOG_DIR"

log() {
  printf '%s %s\n' "$(TZ="$REPORT_TZ" date '+%Y-%m-%dT%H:%M:%S%z')" "$*" | tee -a "$LOG_FILE"
}

if [[ $# -gt 1 ]]; then
  echo "usage: $0 [YYYY-MM]" >&2
  exit 2
fi

if [[ $# -eq 1 ]]; then
  MONTH="$1"
  if ! [[ "$MONTH" =~ ^[0-9]{4}-(0[1-9]|1[0-2])$ ]]; then
    echo "month must be YYYY-MM" >&2
    exit 2
  fi
else
  # Previous month in the report timezone: the 1st of this month minus one day.
  MONTH="$(TZ="$REPORT_TZ" date -d "$(TZ="$REPORT_TZ" date +%Y-%m-01) -1 day" +%Y-%m)"
fi

# Only one run at a time.
exec 9>"${LOG_DIR}/report.lock"
if ! flock -n 9; then
  log "another report run is in progress; exiting"
  exit 1
fi

if ! docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
  log "ERROR container $CONTAINER is not running; report for $MONTH not written"
  exit 1
fi

REPORT="${SCRIPTS}/lshe/lshe_report.py"
if ! docker exec "$CONTAINER" test -f "$REPORT"; then
  log "ERROR $REPORT not found in $CONTAINER: is kolibri_helper_scripts mounted? (LSHE_HELPER_SCRIPTS_DIR in docker/lshe/.env)"
  exit 1
fi

# The output folder is the container's LSHE_REPORT_DIR (/srv/lshe-reports in
# docker-compose.yml), bind-mounted from the host; nothing to create here.
log "start lshe_report.py --month $MONTH"
if docker exec "$CONTAINER" python -u "$REPORT" --month "$MONTH" >>"$LOG_FILE" 2>&1; then
  log "done lshe_report.py --month $MONTH"
else
  status=$?
  log "ERROR lshe_report.py --month $MONTH failed (exit $status); see above"
  exit "$status"
fi

#!/usr/bin/env bash

if [ "$1" = "-h" ] || [ "$1" = "--help" ] || [ $# -eq 0 ]; then
  echo "NAME"
  echo "  run_script - Run an LSHE helper script inside the Kolibri container"
  echo
  echo "SYNOPSIS"
  echo "  ./run_script.sh <script.py> [script options]"
  echo
  echo "DESCRIPTION"
  echo "  Runs on the LSHE centre server (the host). The Python scripts in this"
  echo "  folder need Edulution's Kolibri, which is inside the container: this runs"
  echo "  one of them there, from the copy of this repository mounted in the"
  echo "  container. Add --help after the script name for its own options."
  echo
  echo "  Scripts: set_band_channels.py, set_class_band.py,"
  echo "  import_from_mindspark.py (prefer run_import.sh, which backs up first),"
  echo "  lshe_report.py (prefer run_monthly_report.sh, which logs)."
  echo
  echo "Examples"
  echo "  ./run_script.sh set_band_channels.py --band G4-5 <channel_id> <channel_id>"
  echo "	Sets the channels of band G4-5"
  echo
  echo "  ./run_script.sh set_class_band.py --class \"Grade 5 A\" --band G4-5"
  echo "	Puts the class Grade 5 A in band G4-5"
  echo
  echo "  ./run_script.sh set_class_band.py --list"
  echo "	Shows every band, its channels and classes"
  exit 1
fi

set -euo pipefail

KOLIBRI="${LSHE_KOLIBRI_CONTAINER:-lshe-kolibri}"
# Where docker-compose.yml mounts this repository inside the Kolibri container.
SCRIPTS="${LSHE_HELPER_SCRIPTS_MOUNT:-/opt/kolibri_helper_scripts}"

NAME="$(basename "$1")"
shift
if ! [[ "$NAME" =~ ^[a-z_]+\.py$ ]] || [[ "$NAME" == lshe_common.py ]]; then
  echo "not an LSHE script: $NAME" >&2
  exit 2
fi

if ! docker inspect -f '{{.State.Running}}' "$KOLIBRI" 2>/dev/null | grep -q true; then
  echo "ERROR container $KOLIBRI is not running" >&2
  exit 1
fi
if ! docker exec "$KOLIBRI" test -f "${SCRIPTS}/lshe/${NAME}"; then
  echo "ERROR ${SCRIPTS}/lshe/${NAME} not found in $KOLIBRI: is kolibri_helper_scripts mounted? (LSHE_HELPER_SCRIPTS_DIR in docker/lshe/.env)" >&2
  exit 1
fi

# -t only on a terminal, so colours show by hand but not in logs.
TTY=()
[[ -t 1 ]] && TTY=(-t)
exec docker exec ${TTY[@]+"${TTY[@]}"} "$KOLIBRI" python -u "${SCRIPTS}/lshe/${NAME}" "$@"

#!/usr/bin/env bash

if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  echo "NAME"
  echo "  check_server - Check an LSHE centre server (read-only)"
  echo
  echo "SYNOPSIS"
  echo "  ./check_server.sh"
  echo
  echo "DESCRIPTION"
  echo "  Runs on the LSHE centre server (the host), as root. Changes nothing, and"
  echo "  prints counts and class names only: no learner names, no passwords."
  echo "  Checks the code versions, containers and web page, start-on-boot,"
  echo "  timers (backup, monthly report), settings, migrations, plugins,"
  echo "  content, grade bands and classes, backups, recent errors and disk"
  echo "  space. Each section says what a good result looks like."
  echo
  echo "  Where everything is, and what to do about a problem: the Kolibri"
  echo "  repository's docs/lshe/where-is-what.md."
  exit 1
fi

set -uo pipefail

KOLIBRI_DIR="${LSHE_KOLIBRI_DIR:-/opt/lshe/kolibri}"
SCRIPTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
C="${LSHE_KOLIBRI_CONTAINER:-lshe-kolibri}"
DB="${LSHE_DB_CONTAINER:-lshe-db}"
ENV_FILE="$KOLIBRI_DIR/docker/lshe/.env"
# The address Kolibri is published on, as Docker has it; else from .env.
IP="$(docker port "$C" 8080 2>/dev/null | head -1 | cut -d: -f1)"
[[ -z "$IP" || "$IP" == "0.0.0.0" ]] && IP="$(sed -n 's/^LSHE_BIND_IP=//p' "$ENV_FILE" 2>/dev/null | tail -1 | tr -d '"'"'"' \r')"
IP="${IP:-127.0.0.1}"
h() { printf '\n==== %s ====\n' "$1"; }

h "1. Code (expect the base branches: Kolibri unesco_dev, helper scripts develop)"
git -C "$KOLIBRI_DIR" log -1 --format='Kolibri:        %h %s (%ad)' --date=short
echo "  branch: $(git -C "$KOLIBRI_DIR" rev-parse --abbrev-ref HEAD)"
git -C "$SCRIPTS_DIR" log -1 --format='Helper scripts: %h %s (%ad)' --date=short
echo "  branch: $(git -C "$SCRIPTS_DIR" rev-parse --abbrev-ref HEAD)"

h "2. Containers and web page (expect both Up; HTTP 302 and 200)"
docker ps -a --filter "name=^($C|$DB)$" --format 'table {{.Names}}\t{{.Status}}'
printf 'Kolibri (http://%s:8080/): HTTP %s\n' "$IP" "$(curl -s -o /dev/null -w '%{http_code}' "http://$IP:8080/")"
printf 'Sign-in page: HTTP %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "http://$IP:8080/en/auth/")"

h "3. Start on boot and timers (expect enabled; next backup tonight, report on the 1st)"
for unit in docker.service lshe-kolibri.service lshe-backup.timer lshe-report.timer; do
  enabled="$(systemctl is-enabled "$unit" 2>/dev/null)"
  [[ "$enabled" == "not-found" || -z "$enabled" ]] && enabled="NOT INSTALLED"
  printf '  %-22s %-14s now: %s\n' "$unit" "$enabled" "$(systemctl is-active "$unit" 2>/dev/null)"
done
systemctl list-timers 'lshe-*' --no-pager 2>/dev/null | head -4

h "4. Settings in the container (no secrets printed)"
docker exec "$C" sh -c 'env | grep -E "^(KOLIBRI_CENTRE_CODE|LSHE_GRADE_BANDS|LSHE_DISABLE_ASSESSMENTS|LSHE_REPORT_TIMEZONE|LSHE_TEACHER_CHANNEL_IDS)=" | sort'
docker exec "$C" sh -c 'env | grep -q "^MINDSPARK_MONGO_URI=." && echo "MINDSPARK_MONGO_URI: set (not shown)" || echo "MINDSPARK_MONGO_URI: not set"'

h "5. Fonts and compressed pages (expect wOFF; a number above 50)"
docker exec "$C" sh -c 'f=$(find /kolibri/kolibri/core/static/assets/fonts -name "*.woff" | head -1); printf "small font files: %s of %s\n" "$(find /kolibri/kolibri/core/static/assets/fonts -name "*.woff" -size -1k | wc -l)" "$(find /kolibri/kolibri/core/static/assets/fonts -name "*.woff" | wc -l)"'
docker exec "$C" sh -c 'echo "compressed page files: $(find / -xdev -name "*.js.gz" 2>/dev/null | wc -l)"'

h "6. Database migrations not applied (expect none)"
docker exec "$C" kolibri manage showmigrations 2>/dev/null | grep -B30 '\[ \]' | grep -E '^\S|\[ \]' || echo "All migrations applied"

h "7. Plugins, coach features, bands and classes"
docker exec -i "$C" kolibri shell 2>&1 <<'PY' | grep -v -e "INFO " -e "WARNING " -e "ip: not found"
def main():
    from kolibri.plugins import config
    from kolibri.core.auth.models import Facility, Classroom, Role
    from kolibri.core.auth.constants import role_kinds
    from kolibri.core.content.models import ChannelMetadata
    from kolibri.core.lessons.models import Lesson
    from kolibri.plugins.edulution.models import ClassroomBand, GradeBandChannel
    from kolibri.plugins.edulutionCoach.coach_features import coach_features_by_facility

    active = set(config.ACTIVE_PLUGINS)
    print("Stock learn/coach plugins on (expect False):",
          bool(active & {"kolibri.plugins.learn", "kolibri.plugins.coach"}))
    print("LSHE plugins on (expect True):",
          {"kolibri.plugins.edulution", "kolibri.plugins.edulutionCoach"} <= active)
    features = coach_features_by_facility()
    for f in Facility.objects.all():
        print("Facility %s: coach features %s" % (f.name, features.get(f.id)))
    names = dict(ChannelMetadata.objects.values_list("id", "name"))
    for band in sorted(set(GradeBandChannel.objects.values_list("band", flat=True))):
        chans = GradeBandChannel.objects.filter(band=band).values_list("channel_id", flat=True)
        print("Band %s: %s" % (band, [names.get(c, "MISSING " + c[:8]) for c in chans]))
    bands = dict(ClassroomBand.objects.values_list("classroom_id", "band"))
    facility_coaches = Role.objects.filter(
        kind=role_kinds.COACH, collection__kind="facility").count()
    print("Facility coaches:", facility_coaches)
    print("Classes (band | learners | class coaches | visible lessons):")
    for c in Classroom.objects.order_by("name"):
        learners = c.get_members().filter(roles__isnull=True).count()
        coaches = Role.objects.filter(collection=c, kind=role_kinds.COACH).count()
        lessons = Lesson.objects.filter(collection=c, is_active=True).count()
        flags = [label for label, missing in (
            ("NO BAND", not bands.get(c.id)),
            ("NO COACH", not coaches and not facility_coaches),
            ("NO VISIBLE LESSON", not lessons)) if missing]
        print("  %-25s %-6s %4d %3d %3d  %s" % (
            c.name, bands.get(c.id, "-"), learners, coaches, lessons, " ".join(flags)))

main()
PY

h "8. Content (expect every channel complete)"
docker exec "$C" python -u /opt/kolibri_helper_scripts/lshe/content_status.py 2>&1 || true

h "9. Backups and reports (expect a nightly dump from last night)"
ls -lt /srv/lshe-backups 2>/dev/null | head -4 || echo "/srv/lshe-backups missing"
ls -lt /srv/lshe-reports 2>/dev/null | head -4 || echo "/srv/lshe-reports missing"

h "10. Errors in the last 24 hours (expect none)"
docker logs --since 24h "$C" 2>&1 | grep -E '" 50[0-9] |Traceback|ERROR ' | grep -v "ip: not found" | tail -10
grep -h "ERROR" /var/log/lshe/*.log 2>/dev/null | tail -5
echo "(end of errors)"

h "11. Disk and memory (expect under 80% used)"
df -h / /srv 2>/dev/null | awk '!seen[$0]++'
free -h | head -2
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' "$C" "$DB"

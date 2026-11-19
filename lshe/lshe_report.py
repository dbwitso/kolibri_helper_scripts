"""
Monthly LSHE (Life Skills and Health Education) report for one centre.

    python lshe_report.py --month 2027-03 [--output-dir /srv/lshe-reports]

On an LSHE centre server this runs inside the Kolibri container, from
run_monthly_report.sh (the host's monthly timer). Run that script by hand to
re-run a month; re-runs give the same figures.

Writes two files, overwriting any earlier run for the same month:

    lshe_<CENTRE_CODE>_<YYYY-MM>.csv   aggregate figures, no personal data
    lshe_<CENTRE_CODE>_<YYYY-MM>.json  metadata (generation time, version,
                                       facility ids, timezone, definitions)

CENTRE_CODE comes from $KOLIBRI_CENTRE_CODE; the script fails if it is unset.

Who counts: learners only, i.e. users with no role (not coach, admin or
classroom coach) who are not device superusers. Activity in the teacher
channels ($LSHE_TEACHER_CHANNEL_IDS, or the LSHE_TEACHER_CHANNEL_IDS Django
setting) is left out of every figure.

Metrics, per class and gender, plus a centre total:

    enrolled_learners    learners in the class at the end of the month
    active_learners      learners with at least one session (UserSessionLog)
                         starting in the month
    sessions             sessions (UserSessionLog) starting in the month
    reached_new          learners whose first fully completed item
                         (ContentSummaryLog progress = 1) falls in the month
    reached_cumulative   learners with a fully completed item by the end of
                         the month (UNESCO "reached")
    completions          items reaching progress = 1 in the month, by
                         completion_timestamp
    video_watch_seconds  time_spent in video sessions (ContentSessionLog)
                         ending in the month

Month boundaries are midnight on the 1st in $LSHE_REPORT_TIMEZONE (default
Africa/Lusaka), independent of the container's own timezone.

Class history: Kolibri keeps no history of class memberships, so the first
time a finished month is reported, each learner's class and gender is saved
(LSHEMonthlyEnrolment) and every re-run of that month uses that snapshot.
A month that has not finished yet is reported from current classes and is
not saved.

Needs Edulution's Kolibri fork: the class record (LSHEMonthlyEnrolment) and
the teacher channel setting are in its kolibri.plugins.edulution plugin.
"""
import argparse
import csv
import datetime
import io
import json
import os
import re
import sys
from collections import defaultdict

from lshe_common import ScriptError  # noqa E402 (starts Kolibri)
from lshe_common import ok
from lshe_common import run
from lshe_common import warn
import pytz  # noqa E402
from django.db import transaction  # noqa E402
from django.db.models import Min  # noqa E402

import kolibri  # noqa E402
from kolibri.core.auth.constants.demographics import FEMALE  # noqa E402
from kolibri.core.auth.constants.demographics import MALE  # noqa E402
from kolibri.core.auth.constants.demographics import NOT_SPECIFIED  # noqa E402
from kolibri.core.auth.models import Facility  # noqa E402
from kolibri.core.auth.models import Membership  # noqa E402
from kolibri.plugins.edulution.lshe_metrics import aggregate  # noqa E402
from kolibri.plugins.edulution.lshe_metrics import learner_users  # noqa E402
from kolibri.plugins.edulution.lshe_metrics import normalise_gender  # noqa E402
from kolibri.plugins.edulution.lshe_metrics import per_learner  # noqa E402
from kolibri.plugins.edulution.lshe_settings import teacher_channel_ids  # noqa E402
from kolibri.plugins.edulution.models import LSHEMonthlyEnrolment  # noqa E402

REPORT_VERSION = 1
DEFAULT_TIMEZONE = "Africa/Lusaka"
DEFAULT_OUTPUT_DIR = "/srv/lshe-reports"

GENDERS = (MALE, FEMALE, NOT_SPECIFIED)
ALL = "ALL"
CENTRE_TOTAL = "CENTRE_TOTAL"
NO_CLASS = "NO_CLASS"

METRICS = (
    "enrolled_learners",
    "active_learners",
    "sessions",
    "reached_new",
    "reached_cumulative",
    "completions",
    "video_watch_seconds",
)
COLUMNS = ("centre_code", "month", "class", "gender") + METRICS

CENTRE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def parse_month(value):
    match = MONTH_RE.match(value or "")
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise ScriptError("--month must be YYYY-MM, e.g. 2027-03")
    return int(match.group(1)), int(match.group(2))


def month_bounds(year, month, tz):
    """[start, end) of the month as timezone-aware datetimes in `tz`."""
    start = tz.localize(datetime.datetime(year, month, 1))
    if month == 12:
        end = tz.localize(datetime.datetime(year + 1, 1, 1))
    else:
        end = tz.localize(datetime.datetime(year, month + 1, 1))
    return start, end


def current_enrolment(end):
    """[(user_id, class_name, gender, deleted)] from today's class memberships."""
    learners = learner_users().filter(date_joined__lt=end)
    users = {u["id"]: u for u in learners.values("id", "gender", "deleted")}
    classes = defaultdict(set)
    for user_id, class_name in Membership.objects.filter(
        user_id__in=list(users), collection__kind="classroom"
    ).values_list("user_id", "collection__name"):
        classes[user_id].add(class_name)
    rows = []
    for user_id, u in users.items():
        for class_name in sorted(classes.get(user_id) or [""]):
            rows.append(
                (user_id, class_name, normalise_gender(u["gender"]), u["deleted"])
            )
    return rows


# ---------------------------------------------------------------- enrolment


def get_enrolment(month, end, now):
    """
    Return (rows, snapshot_taken_at or None) for the month. A finished
    month is saved on first use and read back afterwards.
    """
    saved = LSHEMonthlyEnrolment.objects.filter(month=month)
    if saved.exists():
        rows = list(saved.values_list("user_id", "class_name", "gender", "deleted"))
        return rows, saved.aggregate(t=Min("taken_at"))["t"]
    rows = current_enrolment(end)
    if now < end:
        return rows, None  # month not finished: do not freeze it yet
    with transaction.atomic():
        LSHEMonthlyEnrolment.objects.bulk_create(
            LSHEMonthlyEnrolment(
                month=month,
                user_id=user_id,
                class_name=class_name,
                gender=gender,
                deleted=deleted,
                taken_at=now,
            )
            for user_id, class_name, gender, deleted in rows
        )
    return rows, now


# ---------------------------------------------------------------- metrics
# per_learner and aggregate are in kolibri.plugins.edulution.lshe_metrics,
# shared with the coach's Monthly reports page.


def build_rows(centre_code, month, enrolment, data):
    # group -> {user_id: deleted}; a learner in two classes counts in both
    # classes but only once in the centre total.
    groups = defaultdict(dict)
    for user_id, class_name, gender, deleted in enrolment:
        label = class_name or NO_CLASS
        for key in (
            (label, gender),
            (label, ALL),
            (CENTRE_TOTAL, gender),
            (CENTRE_TOTAL, ALL),
        ):
            groups[key][user_id] = deleted and groups[key].get(user_id, True)

    class_labels = sorted({c for c, _ in groups if c != CENTRE_TOTAL})
    rows = []
    for label in class_labels + [CENTRE_TOTAL]:
        for gender in GENDERS + (ALL,):
            figures = aggregate(groups.get((label, gender), {}), data)
            row = {
                "centre_code": centre_code,
                "month": month,
                "class": label,
                "gender": gender,
            }
            row.update(figures)
            rows.append(row)
    return rows


# ----------------------------------------------------------------- output


def write_atomic(path, text):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def lshe_report(month_arg, output_dir, out=None):
    out = out or sys.stdout
    centre_code = os.environ.get("KOLIBRI_CENTRE_CODE", "").strip()
    if not centre_code:
        raise ScriptError("Set KOLIBRI_CENTRE_CODE (e.g. in the deployment .env)")
    if not CENTRE_CODE_RE.match(centre_code):
        raise ScriptError(
            "KOLIBRI_CENTRE_CODE may only contain letters, digits, '_' and '-' (max 32)"
        )
    tz_name = os.environ.get("LSHE_REPORT_TIMEZONE", DEFAULT_TIMEZONE)
    try:
        tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        raise ScriptError("Unknown LSHE_REPORT_TIMEZONE: {}".format(tz_name))

    year, mon = parse_month(month_arg)
    month = "{:04d}-{:02d}".format(year, mon)
    start, end = month_bounds(year, mon, tz)
    now = datetime.datetime.now(pytz.utc)
    if now < start:
        raise ScriptError("{} has not started yet".format(month))

    if not os.path.isdir(output_dir):
        raise ScriptError("Output directory does not exist: {}".format(output_dir))

    try:
        excluded = teacher_channel_ids()
    except ValueError as e:
        raise ScriptError(str(e))
    enrolment, snapshot_at = get_enrolment(month, end, now)
    data = per_learner(list({row[0] for row in enrolment}), start, end, excluded)
    rows = build_rows(centre_code, month, enrolment, data)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)

    base = os.path.join(output_dir, "lshe_{}_{}".format(centre_code, month))
    write_atomic(base + ".csv", buf.getvalue())
    metadata = {
        "report": "lshe_monthly",
        "report_version": REPORT_VERSION,
        "centre_code": centre_code,
        "month": month,
        "timezone": tz_name,
        "month_start": start.isoformat(),
        "month_end_exclusive": end.isoformat(),
        "month_complete": now >= end,
        "class_snapshot_taken_at": snapshot_at.isoformat() if snapshot_at else None,
        "generated_at": now.isoformat(),
        "kolibri_version": kolibri.__version__,
        "facility_ids": sorted(Facility.objects.values_list("id", flat=True)),
        "excluded_teacher_channel_ids": excluded,
        "columns": list(COLUMNS),
        "definitions": {
            "learners": "users with no role who are not device superusers",
            "enrolled_learners": "learners in the class at the end of the month",
            "active_learners": "learners with a UserSessionLog starting in the month",
            "sessions": "UserSessionLog rows starting in the month",
            "reached_new": "learners whose first ContentSummaryLog with progress = 1 is in the month",
            "reached_cumulative": "learners with a ContentSummaryLog with progress = 1 before the end of the month",
            "completions": "ContentSummaryLog rows reaching progress = 1 in the month (completion_timestamp)",
            "video_watch_seconds": "sum of ContentSessionLog.time_spent for kind = video, by end_timestamp in the month",
        },
    }
    write_atomic(base + ".json", json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    if now < end:
        warn(
            "{} has not finished: figures are partial and classes were not "
            "saved.".format(month),
            out,
        )
    ok("Wrote {}.csv and {}.json".format(base, base), out)
    return base


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Write the monthly LSHE report (aggregate figures only) for "
        "this centre."
    )
    parser.add_argument("--month", required=True, help="Month to report, YYYY-MM")
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("LSHE_REPORT_DIR", DEFAULT_OUTPUT_DIR),
        help="Directory for the report files (default: $LSHE_REPORT_DIR or {})".format(
            DEFAULT_OUTPUT_DIR
        ),
    )
    return parser.parse_args(argv)


def main(argv=None, out=None):
    args = parse_args(argv)
    lshe_report(args.month, args.output_dir, out)


if __name__ == "__main__":
    run(main)

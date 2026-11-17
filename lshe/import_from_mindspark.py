"""
Import MindSpark learners, and their classes, into a Kolibri facility.

Reads MindSpark's MongoDB directly (read-only); there is no CSV step.

    python import_from_mindspark.py --dryrun      # report only, change nothing
    python import_from_mindspark.py               # import
    python import_from_mindspark.py --help

On an LSHE centre server, use run_import.sh on the host instead: it runs this
script inside the Kolibri container, after a dry run and a database backup.

The MongoDB address is read from $MINDSPARK_MONGO_URI (preferred: it keeps the
password out of shell history and `ps`) or --mongo-uri.

How it works
- MindSpark is only read (ping and find). Nothing is ever written to it.
- Every Kolibri write goes through Kolibri's models, never SQL, in one
  transaction: a failed run changes nothing.
- Learners are matched on their MindSpark UID, kept in the Kolibri user's
  "ID number" (FacilityUser.id_number), so re-runs update learners in place.
- MindSpark usernames are kept as they are, dots included (kalonda.kamona).
  Edulution's Kolibri allows letters, digits, "_" and ".", up to 30
  characters; any other character becomes "_".
- Passwords are not copied. Learners sign in by username only, so the
  facility must use the formal preset.
- Class and role come from the learner's MindSpark product: the UserProducts
  document whose UPID is UserDetails.products.Mindspark.UPID. Its
  batch.ActiveBatch grade and section give the class ("Grade 7 A"), and its
  category ("student", "teacher") the role. Only "student" accounts are
  imported, unless --staff-pattern is given.
- By default the run is additive: learners deactivated in MindSpark are
  reported, not removed. --remove-inactive takes them out of their classes.
  Accounts and activity are never deleted.

Needs pymongo (in the LSHE Kolibri image) and Edulution's Kolibri fork, for
dots in usernames.
"""
import argparse
import os
import re
import sys
from collections import Counter

from lshe_common import ScriptError  # noqa E402 (starts Kolibri)
from lshe_common import run
from lshe_common import say
from lshe_common import warn
from django.db import transaction  # noqa E402

from kolibri.core.auth.constants.demographics import FEMALE  # noqa E402
from kolibri.core.auth.constants.demographics import MALE  # noqa E402
from kolibri.core.auth.constants.demographics import NOT_SPECIFIED  # noqa E402
from kolibri.core.auth.models import Classroom  # noqa E402
from kolibri.core.auth.models import Facility  # noqa E402
from kolibri.core.auth.models import FacilityUser  # noqa E402
from kolibri.core.auth.models import Membership  # noqa E402

try:
    from pymongo import MongoClient
except ImportError:  # pragma: no cover
    MongoClient = None


GENDER_MAP = {
    "boy": MALE,
    "girl": FEMALE,
    "m": MALE,
    "male": MALE,
    "f": FEMALE,
    "female": FEMALE,
}

# UserProducts.category of accounts that are imported as learners.
LEARNER_CATEGORY = "student"

USERNAME_MAX = 30
FULL_NAME_MAX = 120
CLASS_NAME_MAX = 100


class DryRunRollback(Exception):
    pass


def map_gender(value):
    return GENDER_MAP.get((value or "").strip().lower(), NOT_SPECIFIED)


def birth_year(dob):
    """MindSpark dateOfBirth 'YYYY-MM-DD' -> Kolibri 4-digit year, or NOT_SPECIFIED."""
    year = str(dob or "")[:4]
    if year.isdigit() and 1900 < int(year) < 2100:
        return year
    return NOT_SPECIFIED


def kolibri_username(ms_username):
    """Make a MindSpark username valid for Kolibri: [A-Za-z0-9_.], max 30.

    Dots are kept so learners sign in with their exact MindSpark username.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_.]", "_", (ms_username or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:USERNAME_MAX]


def full_name(details):
    name = (details.get("name") or "").strip()
    if not name:
        parts = [details.get("firstName") or "", details.get("lastName") or ""]
        name = " ".join(p.strip() for p in parts if p and p.strip())
    return name[:FULL_NAME_MAX]


def _mindspark_upids(um_db):
    """{UID: UPID of the learner's MindSpark product} from UserDetails."""
    upids = {}
    for d in um_db.UserDetails.find({}, {"UID": 1, "products.Mindspark.UPID": 1}):
        upid = ((d.get("products") or {}).get("Mindspark") or {}).get("UPID")
        if d.get("UID") and upid:
            upids[str(d["UID"])] = str(upid)
    return upids


def mindspark_products(um_db):
    """
    {UID: that learner's MindSpark UserProducts document}.

    A user can hold several products; the one whose UPID matches
    UserDetails.products.Mindspark.UPID is used. Without that link, the
    first product found is used.
    """
    upids = _mindspark_upids(um_db)
    products = {}
    fields = {"UID": 1, "UPID": 1, "category": 1, "batch.ActiveBatch": 1}
    for product in um_db.UserProducts.find({}, fields):
        uid = str(product.get("UID") or "")
        if not uid:
            continue
        upid = str(product.get("UPID") or product.get("_id") or "")
        wanted = upids.get(uid)
        if wanted is not None and upid != wanted:
            continue
        if wanted is None and uid in products:
            continue
        products[uid] = product
    return products


def class_name_for(product):
    """'Grade <grade> <section>' from batch.ActiveBatch, or None."""
    batch = (product.get("batch") or {}).get("ActiveBatch") or {}
    grade = str(batch.get("grade") or "").strip()
    section = str(batch.get("section") or "").strip()
    if not grade:
        return None
    name = "Grade {} {}".format(grade, section) if section else "Grade {}".format(grade)
    return name[:CLASS_NAME_MAX]


def build_class_map(um_db, products=None):
    """Return {mindspark_UID: kolibri_class_name}, e.g. "Grade 7 A"."""
    if products is None:
        products = mindspark_products(um_db)
    names = {uid: class_name_for(p) for uid, p in products.items()}
    return {uid: name for uid, name in names.items() if name}


def build_role_map(um_db, products=None):
    """Return {mindspark_UID: UserProducts.category}, e.g. "student"."""
    if products is None:
        products = mindspark_products(um_db)
    return {
        uid: str(p.get("category") or "").strip().lower() for uid, p in products.items()
    }


def get_facility(facility_id):
    if facility_id:
        try:
            return Facility.objects.get(pk=facility_id)
        except (Facility.DoesNotExist, ValueError):
            raise ScriptError("Facility {} not found".format(facility_id))
    facility = Facility.get_default_facility()
    if not facility:
        raise ScriptError("No default facility; pass --facility")
    return facility


def connect(uri):
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except Exception as e:
        raise ScriptError("Cannot reach MindSpark Mongo: {}".format(e))
    return client


def load_usernames(auth):
    """{UID (as a string): MindSpark username} from UserAuthentication."""
    return {
        str(d["UID"]): d.get("username")
        for d in auth.Authentication.find({}, {"UID": 1, "username": 1})
        if d.get("UID")
    }


class MindSparkImporter(object):
    """One import run into one facility. Counts go in self.stats."""

    def __init__(self, facility, roles, class_map, staff_re=None, out=None):
        self.facility = facility
        self.roles = roles
        self.class_map = class_map
        self.staff_re = staff_re
        self.out = out or sys.stdout
        self.stats = Counter()
        self.staff_already_imported = []
        self.classrooms = {}
        self.taken = set()

    # --------------------------------------------------------------- helpers

    def unique_username(self, wanted, uid):
        """Avoid collisions with other users (not the one keyed on this UID)."""
        candidate, n = wanted, 1
        while (
            candidate in self.taken
            or FacilityUser.objects.filter(
                facility=self.facility, username__iexact=candidate
            )
            .exclude(id_number=uid)
            .exists()
        ):
            n += 1
            suffix = "_{}".format(n)
            candidate = wanted[: USERNAME_MAX - len(suffix)] + suffix
        return candidate

    def get_classroom(self, name):
        name = name[:CLASS_NAME_MAX]
        if name not in self.classrooms:
            classroom = Classroom.objects.filter(
                parent=self.facility, name=name
            ).first()
            if classroom is None:
                classroom = Classroom.objects.create(parent=self.facility, name=name)
                self.stats["classes_created"] += 1
            self.classrooms[name] = classroom
        return self.classrooms[name]

    def set_class(self, user, class_name):
        """Put user in exactly one classroom: class_name (or none)."""
        current = Membership.objects.filter(user=user, collection__kind="classroom")
        target = self.get_classroom(class_name) if class_name else None
        for membership in current:
            if target is None or membership.collection_id != target.id:
                membership.delete()
                self.stats["class_removed"] += 1
        if target is not None and not current.filter(collection=target).exists():
            target.add_member(user)
            self.stats["class_added"] += 1

    def resolve_username(self, wanted, uid, existing, ms_username):
        if existing and existing.username == wanted:
            username = wanted
        else:
            username = self.unique_username(wanted, uid)
        if username != ms_username:
            self.stats["username_changed"] += 1
        self.taken.add(username)
        return username

    def upsert_user(self, uid, fields, existing):
        if existing:
            changed = [k for k, v in fields.items() if getattr(existing, k) != v]
            if changed:
                for k in changed:
                    setattr(existing, k, fields[k])
                existing.full_clean()
                existing.save()
                self.stats["updated"] += 1
            else:
                self.stats["unchanged"] += 1
            return existing
        # Same as FacilityUserModelManager.create_user with no password:
        # the literal NOT_SPECIFIED marks a passwordless account.
        user = FacilityUser(
            facility=self.facility, id_number=uid, password=NOT_SPECIFIED, **fields
        )
        user.full_clean()
        user.save()
        self.stats["created"] += 1
        return user

    def is_staff(self, uid, ms_username):
        """
        Whether to skip this account as staff. --staff-pattern decides when
        given; otherwise only MindSpark "student" products are learners.
        Returns None when the account has no MindSpark product at all.
        """
        if self.staff_re is not None:
            return bool(self.staff_re.search(ms_username))
        role = self.roles.get(uid)
        if role is None:
            return None
        return role != LEARNER_CATEGORY

    # ------------------------------------------------------------------ main

    def import_one(self, d, usernames, remove_inactive):
        # UID may be stored as an ObjectId or a number; id_number is a string.
        uid = d.get("UID") or d.get("_id")
        uid = str(uid) if uid else None
        ms_username = usernames.get(uid)
        if not uid or not ms_username:
            self.stats["skipped_no_username"] += 1
            return
        existing = FacilityUser.objects.filter(
            facility=self.facility, id_number=uid
        ).first()

        staff = self.is_staff(uid, ms_username)
        if staff is None:
            self.stats["skipped_no_mindspark_product"] += 1
            return
        if staff:
            self.stats["skipped_staff"] += 1
            if existing:
                self.staff_already_imported.append(existing.username)
            return

        if d.get("status") != "A":
            self.stats["inactive_in_mindspark"] += 1
            if existing and remove_inactive:
                self.set_class(existing, None)
                self.stats["inactive_removed_from_class"] += 1
            return

        wanted = kolibri_username(ms_username)
        if not wanted:
            self.stats["skipped_bad_username"] += 1
            return
        fields = {
            "username": self.resolve_username(wanted, uid, existing, ms_username),
            "full_name": full_name(d),
            "gender": map_gender(d.get("gender")),
            "birth_year": birth_year(d.get("dateOfBirth")),
        }

        user = self.upsert_user(uid, fields, existing)

        if self.class_map:
            class_name = self.class_map.get(uid)
            if class_name:
                self.set_class(user, class_name)
            else:
                self.stats["no_class_in_mindspark"] += 1

    def report(self):
        for key in sorted(self.stats):
            say("{:<28} {}".format(key, self.stats[key]), self.out)
        if not self.staff_already_imported:
            return
        warn(
            "These Kolibri accounts are staff in MindSpark but were imported "
            "earlier as learners. The import leaves them alone; remove them in "
            "Facility > Users, or make them coaches:",
            self.out,
        )
        for username in sorted(self.staff_already_imported):
            say("  {}".format(username), self.out)


def warn_if_passwords_required(facility, out):
    if not facility.dataset.learner_can_login_with_no_password:
        warn(
            "Facility '{}' requires passwords. Imported learners get no usable "
            "password; switch the facility to passwordless (formal preset) or "
            "they will not be able to sign in.".format(facility.name),
            out,
        )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Import learners and classes from MindSpark (MongoDB, "
        "read-only) into a Kolibri facility.",
        epilog="On an LSHE server, run run_import.sh on the host instead.",
    )
    parser.add_argument(
        "--mongo-uri",
        default=None,
        help="MindSpark MongoDB URI (default: $MINDSPARK_MONGO_URI)",
    )
    parser.add_argument(
        "--facility",
        default=None,
        help="Kolibri facility id (default: the device's default facility)",
    )
    parser.add_argument(
        "--um-db", default="UserManagement", help="MindSpark user database"
    )
    parser.add_argument(
        "--auth-db", default="UserAuthentication", help="MindSpark login database"
    )
    parser.add_argument(
        "--org-id", default=None, help="Only import learners with this MindSpark orgID"
    )
    parser.add_argument(
        "--staff-pattern",
        default=None,
        help=(
            "Regex on the MindSpark username for accounts to skip. When given, it "
            "replaces the default rule (import only UserProducts.category "
            "'student')."
        ),
    )
    parser.add_argument(
        "--remove-inactive",
        action="store_true",
        help="Remove MindSpark-deactivated learners from their Kolibri classes",
    )
    parser.add_argument(
        "--dryrun", action="store_true", help="Do everything, report, then roll back"
    )
    return parser.parse_args(argv)


def import_from_mindspark(args, out=None):
    out = out or sys.stdout
    if MongoClient is None:
        raise ScriptError("pymongo is not installed in this Kolibri environment")
    uri = args.mongo_uri or os.environ.get("MINDSPARK_MONGO_URI")
    if not uri:
        raise ScriptError("Set $MINDSPARK_MONGO_URI or pass --mongo-uri")

    facility = get_facility(args.facility)
    warn_if_passwords_required(facility, out)

    client = connect(uri)
    um = client[args.um_db]
    auth = client[args.auth_db]
    staff_re = re.compile(args.staff_pattern, re.I) if args.staff_pattern else None

    usernames = load_usernames(auth)
    products = mindspark_products(um)
    class_map = build_class_map(um, products)
    importer = MindSparkImporter(
        facility, build_role_map(um, products), class_map, staff_re, out
    )
    if not class_map:
        warn(
            "No classes found in MindSpark (UserProducts.batch.ActiveBatch): "
            "learners will be imported without classes and existing class "
            "memberships left alone.",
            out,
        )

    query = {"orgID": args.org_id} if args.org_id else {}
    try:
        with transaction.atomic():
            for d in um.UserDetails.find(query):
                importer.import_one(d, usernames, args.remove_inactive)
            if args.dryrun:
                raise DryRunRollback()
    except DryRunRollback:
        say("Dry run: all changes rolled back.", out)
    finally:
        client.close()

    importer.report()
    return importer


def main(argv=None, out=None):
    import_from_mindspark(parse_args(argv), out)


if __name__ == "__main__":
    run(main)

"""
Set, or clear, the LSHE grade band of a class.

    python set_class_band.py --class "Grade 5 A" --band G4-5
    python set_class_band.py --class-id <classroom_id> --band G6-7
    python set_class_band.py --class "Grade 5 A" --clear
    python set_class_band.py --list

Run it inside the LSHE Kolibri container, for example:

    docker exec lshe-kolibri python /opt/kolibri_helper_scripts/lshe/set_class_band.py --list

A class with no band can receive no lesson or quiz assignments. Changing a
band does not change lessons or quizzes already saved; learners simply stop
seeing content outside the new band's channels. Set the band's channels with
set_band_channels.py.
"""
import argparse
import sys

from lshe_common import ScriptError  # noqa E402 (starts Kolibri)
from lshe_common import ok
from lshe_common import run
from lshe_common import warn
from set_band_channels import print_bands  # noqa E402
from set_band_channels import validate_band  # noqa E402

from kolibri.core.auth.models import Classroom  # noqa E402
from kolibri.core.auth.models import Facility  # noqa E402
from kolibri.plugins.edulution.models import ClassroomBand  # noqa E402
from kolibri.plugins.edulution.models import GradeBandChannel  # noqa E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Set the LSHE grade band of a class.")
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--class", dest="class_name", help="Class name, e.g. 'Grade 5 A'"
    )
    target.add_argument("--class-id", help="Class id (for classes that share a name)")
    parser.add_argument(
        "--facility", help="Facility id (default: the device's default facility)"
    )
    parser.add_argument("--band", help="Band name, e.g. G4-5")
    parser.add_argument("--clear", action="store_true", help="Remove the class's band")
    parser.add_argument(
        "--list", action="store_true", help="Show every band, its channels and classes"
    )
    return parser.parse_args(argv)


def get_classroom(class_name=None, class_id=None, facility_id=None):
    if class_id:
        classroom = Classroom.objects.filter(id=class_id).first()
        if classroom is None:
            raise ScriptError("No class with id {}".format(class_id))
        return classroom
    if not class_name:
        raise ScriptError("Give --class or --class-id")
    if facility_id:
        facility = Facility.objects.filter(id=facility_id).first()
    else:
        facility = Facility.get_default_facility()
    if facility is None:
        raise ScriptError("Facility not found; pass --facility")
    matches = list(Classroom.objects.filter(parent=facility, name=class_name))
    if not matches:
        raise ScriptError('No class named "{}" in {}'.format(class_name, facility.name))
    if len(matches) > 1:
        raise ScriptError(
            'Several classes are named "{}"; use --class-id ({})'.format(
                class_name, ", ".join(c.id for c in matches)
            )
        )
    return matches[0]


def set_class_band(classroom, band, out=None):
    """Put classroom in band, or take it out of any band when band is None."""
    out = out or sys.stdout
    if band is None:
        ClassroomBand.objects.filter(classroom=classroom).delete()
        warn(
            '"{}" has no band now: it can receive no assignments.'.format(
                classroom.name
            ),
            out,
        )
        return
    band = validate_band(band)
    ClassroomBand.objects.update_or_create(classroom=classroom, defaults={"band": band})
    ok('"{}" is now in band {}.'.format(classroom.name, band), out)
    if not GradeBandChannel.objects.filter(band=band).exists():
        warn("Band {} has no channels yet; run set_band_channels.py.".format(band), out)


def main(argv=None, out=None):
    args = parse_args(argv)
    if args.list:
        print_bands(out)
        return
    if args.clear and args.band:
        raise ScriptError("Give --band or --clear (not both)")
    classroom = get_classroom(args.class_name, args.class_id, args.facility)
    if args.clear:
        set_class_band(classroom, None, out)
    else:
        set_class_band(classroom, args.band, out)


if __name__ == "__main__":
    run(main)

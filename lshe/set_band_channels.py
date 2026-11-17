"""
Set the channels allowed for an LSHE grade band, replacing any earlier list.

    python set_band_channels.py --band G4-5 <channel_id> [<channel_id> ...]
    python set_band_channels.py --band G4-5 --clear
    python set_band_channels.py --list

Run it inside the LSHE Kolibri container, for example:

    docker exec lshe-kolibri python /opt/kolibri_helper_scripts/lshe/set_band_channels.py --list

Learners only ever see assigned content from their class's band channels,
and coaches can only assign content from those channels (enforced by
Edulution's Kolibri, plugin kolibri.plugins.edulution). Teacher channels
(LSHE_TEACHER_CHANNEL_IDS) are refused: content in a band can reach
learners, and teacher channels must never reach learners.
"""
import argparse
import re
import sys

from lshe_common import ScriptError  # noqa E402 (starts Kolibri)
from lshe_common import ok
from lshe_common import run
from lshe_common import say
from lshe_common import warn
from django.db import transaction  # noqa E402

from kolibri.core.auth.models import Classroom  # noqa E402
from kolibri.core.content.models import ChannelMetadata  # noqa E402
from kolibri.plugins.edulution.lshe_settings import normalise_channel_id  # noqa E402
from kolibri.plugins.edulution.lshe_settings import teacher_channel_ids  # noqa E402
from kolibri.plugins.edulution.models import BAND_NAME_MAX  # noqa E402
from kolibri.plugins.edulution.models import ClassroomBand  # noqa E402
from kolibri.plugins.edulution.models import GradeBandChannel  # noqa E402

BAND_RE = re.compile(r"^[A-Za-z0-9_-]{1,%d}$" % BAND_NAME_MAX)


def validate_band(band):
    if not band or not BAND_RE.match(band):
        raise ScriptError(
            "--band may only contain letters, digits, '-' and '_' (max {})".format(
                BAND_NAME_MAX
            )
        )
    return band


def print_bands(out=None):
    """Every band, with its channels and classes."""
    out = out or sys.stdout
    names = dict(ChannelMetadata.objects.values_list("id", "name"))
    bands = sorted(
        set(GradeBandChannel.objects.values_list("band", flat=True))
        | set(ClassroomBand.objects.values_list("band", flat=True))
    )
    if not bands:
        say("No grade bands set up yet.", out)
    for band in bands:
        say(band, out)
        channels = GradeBandChannel.objects.filter(band=band).order_by("channel_id")
        for channel_id in channels.values_list("channel_id", flat=True):
            say(
                "  channel {} {}".format(
                    channel_id, names.get(channel_id, "(not imported on this server)")
                ),
                out,
            )
        if not channels.exists():
            say("  no channels: nothing can be assigned to its classes", out)
        for class_name in (
            ClassroomBand.objects.filter(band=band)
            .order_by("classroom__name")
            .values_list("classroom__name", flat=True)
        ):
            say("  class   {}".format(class_name), out)
    unbanded = Classroom.objects.filter(grade_band_link__isnull=True).order_by("name")
    if unbanded.exists():
        say("No band (these classes can receive no assignments):", out)
        for name in unbanded.values_list("name", flat=True):
            say("  class   {}".format(name), out)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Set the channels allowed for an LSHE grade band."
    )
    parser.add_argument("channel_ids", nargs="*", help="Channel ids for the band")
    parser.add_argument("--band", help="Band name, e.g. G4-5")
    parser.add_argument(
        "--clear", action="store_true", help="Remove all channels from the band"
    )
    parser.add_argument(
        "--list", action="store_true", help="Show every band, its channels and classes"
    )
    return parser.parse_args(argv)


def set_band_channels(band, channel_ids, out=None):
    """Replace the band's channel list with channel_ids (empty clears it)."""
    out = out or sys.stdout
    band = validate_band(band)
    try:
        channel_ids = sorted({normalise_channel_id(c) for c in channel_ids})
        teacher = set(teacher_channel_ids())
    except ValueError as e:
        raise ScriptError("Invalid channel id: {}".format(e))
    refused = sorted(set(channel_ids) & teacher)
    if refused:
        raise ScriptError(
            "Teacher channels cannot be in a grade band: {}".format(", ".join(refused))
        )

    with transaction.atomic():
        GradeBandChannel.objects.filter(band=band).delete()
        GradeBandChannel.objects.bulk_create(
            GradeBandChannel(band=band, channel_id=c) for c in channel_ids
        )

    imported = set(ChannelMetadata.objects.values_list("id", flat=True))
    for channel_id in channel_ids:
        if channel_id not in imported:
            warn(
                "Channel {} is not imported on this server yet.".format(channel_id), out
            )
    ok("Band {} now allows {} channel(s).".format(band, len(channel_ids)), out)


def main(argv=None, out=None):
    args = parse_args(argv)
    if args.list:
        print_bands(out)
        return
    if args.clear == bool(args.channel_ids):
        raise ScriptError("Give channel ids, or --clear (not both)")
    set_band_channels(args.band, args.channel_ids, out)


if __name__ == "__main__":
    run(main)

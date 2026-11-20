"""
What content this LSHE server has, and whether any of it is missing.

    python content_status.py           every channel
    python content_status.py --files   also check each file is on disk (slower)

Run it inside the LSHE Kolibri container, for example with
``./run_script.sh content_status.py``. Read-only.

For each channel: its version, grade band (or teacher channel), how many of
its resources are available to learners, and how many of its files are
missing. Also warns about a grade band naming a channel that is not on the
server, and a teacher channel that is not on it. Exits 1 if anything is
missing, so a script can tell.
"""
import argparse
import os

from lshe_common import ScriptError  # noqa E402 (starts Kolibri)
from lshe_common import ok
from lshe_common import run
from lshe_common import say
from lshe_common import warn

from django.db.models import Sum  # noqa E402
from kolibri.core.content.models import ChannelMetadata  # noqa E402
from kolibri.core.content.models import ContentNode  # noqa E402
from kolibri.core.content.models import LocalFile  # noqa E402
from kolibri.core.content.utils.paths import get_content_storage_file_path  # noqa E402
from kolibri.plugins.edulution.lshe_settings import teacher_channel_ids  # noqa E402
from kolibri.plugins.edulution.models import GradeBandChannel  # noqa E402

TOPIC = "topic"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Show this server's LSHE content.")
    parser.add_argument(
        "--files",
        action="store_true",
        help="also check that every file the database lists is on disk",
    )
    return parser.parse_args(argv)


def channel_files(channel_id):
    return LocalFile.objects.filter(
        files__contentnode__channel_id=channel_id
    ).distinct()


def missing_on_disk(files):
    """Files marked available whose file is not on disk."""
    missing = 0
    for file_id, extension in files.filter(available=True).values_list(
        "id", "extension"
    ):
        path = get_content_storage_file_path("{}.{}".format(file_id, extension))
        if not os.path.isfile(path):
            missing += 1
    return missing


def main(argv=None):
    args = parse_args(argv)
    try:
        teachers = set(teacher_channel_ids())
    except ValueError as e:
        raise ScriptError("LSHE_TEACHER_CHANNEL_IDS: {}".format(e))
    bands = dict(GradeBandChannel.objects.values_list("channel_id", "band"))
    channels = list(ChannelMetadata.objects.order_by("name"))
    problems = 0

    if not channels:
        warn("No channels on this server. Import some with import_channels.sh.")
        return 1

    for channel in channels:
        resources = ContentNode.objects.filter(channel_id=channel.id).exclude(
            kind=TOPIC
        )
        total = resources.count()
        available = resources.filter(available=True).count()
        files = channel_files(channel.id)
        files_total = files.count()
        files_ready = files.filter(available=True).count()
        size_mb = (
            files.filter(available=True).aggregate(s=Sum("file_size"))["s"] or 0
        ) // (1024 * 1024)
        if channel.id in teachers:
            role = "teacher channel"
        elif channel.id in bands:
            role = "band " + bands[channel.id]
        else:
            role = "NO BAND (learners cannot be given it)"
        say("")
        say("{}  {}".format(channel.id, channel.name))
        say("  version {}, {}".format(channel.version, role))
        say("  resources available: {} of {}".format(available, total))
        say(
            "  files: {} of {} on the server, {} MB".format(
                files_ready, files_total, size_mb
            )
        )
        if args.files:
            gone = missing_on_disk(files)
            if gone:
                warn("{} file(s) are listed but not on disk".format(gone))
                problems += 1
            else:
                say("  every listed file is on disk")
        if available < total or files_ready < files_total:
            warn("this channel is not complete: run import_channels.sh for it again")
            problems += 1
        if channel.id not in teachers and channel.id not in bands:
            problems += 1

    present = {c.id for c in channels}
    say("")
    for channel_id, band in sorted(bands.items()):
        if channel_id not in present:
            warn(
                "band {} names channel {}, which is not on this server".format(
                    band, channel_id
                )
            )
            problems += 1
    for channel_id in sorted(teachers - present):
        warn(
            "teacher channel {} (LSHE_TEACHER_CHANNEL_IDS) is not on this server".format(
                channel_id
            )
        )
        problems += 1

    if problems:
        warn("{} problem(s) found".format(problems))
        return 1
    ok("All {} channel(s) are complete.".format(len(channels)))
    return 0


if __name__ == "__main__":
    import sys

    def _main():
        code = main()
        if code:
            sys.exit(code)

    run(_main)

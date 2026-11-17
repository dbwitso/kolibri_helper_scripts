"""Shared set-up for the LSHE helper scripts in this folder.

Importing this module starts Kolibri (``import kolibri`` then
``django.setup()``), like the other helper scripts, so it must be imported
before any Kolibri model. The scripts need Edulution's Kolibri fork with the
``kolibri.plugins.edulution`` plugin (grade bands, the monthly class record),
so they run inside the LSHE Kolibri container. See README.md.
"""
import os
import sys

LSHE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(LSHE_DIR)

# colors.py and helpers.py live in the repository root.
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

import logging  # noqa E402

# Kolibri logs a dozen INFO/WARNING lines while it starts (settings, network
# interfaces). Hide them so the script's own output is readable; errors still
# show.
logging.disable(logging.WARNING)
try:
    import kolibri  # noqa F401 E402
    import django  # noqa E402

    django.setup()
finally:
    logging.disable(logging.NOTSET)

from colors import colors  # noqa E402


class ScriptError(Exception):
    """A problem the operator must fix. The script prints it and exits 1."""


def _colored(text, color, stream):
    # Colours only on a terminal: the wrappers write the output to log files.
    if stream.isatty():
        return "{}{}{}".format(color, text, colors.reset)
    return text


def say(text="", stream=None):
    stream = stream or sys.stdout
    stream.write("{}\n".format(text))


def warn(text, stream=None):
    stream = stream or sys.stdout
    say(_colored("WARNING: {}".format(text), colors.fg.yellow, stream), stream)


def ok(text, stream=None):
    stream = stream or sys.stdout
    say(_colored(text, colors.fg.lightgreen, stream), stream)


def run(main):
    """Run ``main()``; print a ScriptError in red and exit 1."""
    try:
        main()
    except ScriptError as e:
        say(_colored("ERROR: {}".format(e), colors.fg.red, sys.stderr), sys.stderr)
        sys.exit(1)

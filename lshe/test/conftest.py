"""
pytest set-up for the LSHE script tests.

These are Django tests against Edulution's Kolibri, so run them where that
Kolibri is installed with its test requirements (see readme.md here).
"""
import os
import sys

LSHE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if LSHE_DIR not in sys.path:
    sys.path.insert(0, LSHE_DIR)

# Kolibri's own fixtures: a temporary KOLIBRI_HOME and database clean-up.
from kolibri.conftest import django_db_setup  # noqa E402 F401
from kolibri.conftest import global_fixture  # noqa E402 F401

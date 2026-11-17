"""
Tests for set_band_channels.py and set_class_band.py. All data is invented.

The rules themselves (what coaches can assign, what learners see) are
enforced and tested in Edulution's Kolibri; these tests cover the scripts.
"""
import uuid

from django.test import override_settings
from django.test import TestCase
from six import StringIO

from kolibri.core.auth.models import Classroom
from kolibri.core.auth.models import Facility
from kolibri.core.auth.test.helpers import provision_device
from kolibri.core.content.models import ChannelMetadata
from kolibri.core.content.models import ContentNode
from kolibri.plugins.edulution.models import ClassroomBand
from kolibri.plugins.edulution.models import GradeBandChannel

import set_band_channels  # noqa E402
import set_class_band  # noqa E402
from lshe_common import ScriptError  # noqa E402


def make_channel(name, tree_id):
    channel_id = uuid.uuid4().hex
    root = ContentNode.objects.create(
        id=uuid.uuid4().hex,
        channel_id=channel_id,
        content_id=uuid.uuid4().hex,
        parent=None,
        title=name,
        kind="topic",
        available=True,
        tree_id=tree_id,
        level=0,
        lft=1,
        rght=2,
    )
    ChannelMetadata.objects.create(
        id=channel_id, name=name, root=root, min_schema_version="1", version=1
    )
    return channel_id


class GradeBandScriptsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.facility = Facility.objects.create(name="Centre")
        cls.lower = Classroom.objects.create(name="Grade 4 A", parent=cls.facility)
        cls.upper = Classroom.objects.create(name="Grade 6 A", parent=cls.facility)
        cls.unbanded = Classroom.objects.create(name="Grade 5 B", parent=cls.facility)
        cls.g45_channel = make_channel("LSHE G4-5", 1)
        cls.g67_channel = make_channel("LSHE G6-7", 2)
        GradeBandChannel.objects.create(band="G4-5", channel_id=cls.g45_channel)
        GradeBandChannel.objects.create(band="G6-7", channel_id=cls.g67_channel)
        ClassroomBand.objects.create(classroom=cls.lower, band="G4-5")
        ClassroomBand.objects.create(classroom=cls.upper, band="G6-7")

    def run_script(self, script, *args):
        out = StringIO()
        script.main(list(args), out)
        return out.getvalue()

    def band_channels(self, band):
        return set(
            GradeBandChannel.objects.filter(band=band).values_list(
                "channel_id", flat=True
            )
        )

    def test_set_band_channels_replaces_list(self):
        new = uuid.uuid4()
        out = self.run_script(
            set_band_channels, "--band", "G4-5", str(new), self.g45_channel
        )
        # Dashed ids are stored in Kolibri's 32-character form.
        self.assertEqual(self.band_channels("G4-5"), {new.hex, self.g45_channel})
        self.assertIn("not imported", out)
        self.assertIn("now allows 2 channel(s)", out)

    def test_set_band_channels_clear(self):
        self.run_script(set_band_channels, "--band", "G4-5", "--clear")
        self.assertEqual(self.band_channels("G4-5"), set())

    def test_set_band_channels_refuses_teacher_channel(self):
        teacher = uuid.uuid4().hex
        with override_settings(LSHE_TEACHER_CHANNEL_IDS=teacher):
            with self.assertRaises(ScriptError):
                self.run_script(set_band_channels, "--band", "G4-5", teacher)
        self.assertEqual(self.band_channels("G4-5"), {self.g45_channel})

    def test_set_band_channels_rejects_bad_input(self):
        for args in (
            ["--band", "G4-5", "not-a-uuid"],
            ["--band", "bad band!", self.g45_channel],
            ["--band", "G4-5"],
            ["--band", "G4-5", "--clear", self.g45_channel],
        ):
            with self.assertRaises(ScriptError):
                self.run_script(set_band_channels, *args)
        self.assertEqual(self.band_channels("G4-5"), {self.g45_channel})

    def test_set_class_band_by_name_and_clear(self):
        args = ["--class", "Grade 5 B", "--facility", self.facility.id]
        self.run_script(set_class_band, *(args + ["--band", "G4-5"]))
        self.assertEqual(
            ClassroomBand.objects.get(classroom=self.unbanded).band, "G4-5"
        )
        self.run_script(set_class_band, *(args + ["--band", "G6-7"]))
        self.assertEqual(
            ClassroomBand.objects.get(classroom=self.unbanded).band, "G6-7"
        )
        out = self.run_script(set_class_band, "--class-id", self.unbanded.id, "--clear")
        self.assertFalse(ClassroomBand.objects.filter(classroom=self.unbanded).exists())
        self.assertIn("can receive no assignments", out)

    def test_set_class_band_default_facility(self):
        provision_device(default_facility=self.facility)
        self.run_script(set_class_band, "--class", "Grade 5 B", "--band", "G4-5")
        self.assertEqual(
            ClassroomBand.objects.get(classroom=self.unbanded).band, "G4-5"
        )

    def test_set_class_band_warns_on_empty_band(self):
        out = self.run_script(
            set_class_band, "--class-id", self.unbanded.id, "--band", "G8-9"
        )
        self.assertIn("has no channels yet", out)

    def test_set_class_band_errors(self):
        for args in (
            ["--class", "Nope", "--band", "G4-5", "--facility", self.facility.id],
            ["--class-id", "0" * 32, "--band", "G4-5"],
            ["--band", "G4-5"],
            ["--class-id", self.unbanded.id, "--band", "G4-5", "--clear"],
        ):
            with self.assertRaises(ScriptError):
                self.run_script(set_class_band, *args)

    def test_set_class_band_same_name_needs_id(self):
        Classroom.objects.create(name="Grade 4 A", parent=self.facility)
        with self.assertRaises(ScriptError) as raised:
            self.run_script(
                set_class_band,
                "--class",
                "Grade 4 A",
                "--band",
                "G4-5",
                "--facility",
                self.facility.id,
            )
        self.assertIn("--class-id", str(raised.exception))

    def test_list(self):
        out = self.run_script(set_class_band, "--list")
        self.assertIn("G4-5", out)
        self.assertIn("Grade 4 A", out)
        self.assertIn("LSHE G6-7", out)
        # The class with no band is called out.
        self.assertIn("No band", out)
        self.assertIn("Grade 5 B", out.split("No band", 1)[1])

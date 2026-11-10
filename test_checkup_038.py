"""Tests for checkup 0.3.8: the compass check (compass.py, from the pre-registered magcurrent-2026
census) joins the report as a last section, and a strong coupling counts as a flag.

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""
import json
import sys
import types
import unittest
from unittest import mock

import checkup

QUIET = {"health_ok": False, "health_reason": "under 60 s in the air"}
FLAG = {"compass": "0.1.0", "level": "flag", "text": "Compass tracks battery current: X.", "coupling_pct": 17.0,
        "class": "strong", "fleet_place": "p75-p90", "cal_mag_comp_typ": 0}
NOTE = dict(FLAG, level="note", text="Compass moves with battery current: Y.", **{"class": "moderate"})
OK = dict(FLAG, level="ok", text="Compass barely moves with battery current: Z.", **{"class": "weak"})


def report(compass=None):
    r = {"log": "/logs/a.ulg", "checkup": checkup.__version__, "health": dict(QUIET),
         "gps": {"skipped": "gps_lag.py and innov.py not found (run fetch_deps.py)"}, "gps_verdict": None}
    if compass is not None:
        r["compass"] = compass
    return r


class Version(unittest.TestCase):
    def test_version(self):
        self.assertEqual(checkup.__version__, "0.3.9")


class Render(unittest.TestCase):
    def test_section_comes_last(self):
        md = checkup.render(report(OK))
        self.assertIn("### Compass\n\nCompass barely moves with battery current: Z.\n", md)
        self.assertLess(md.index("### GPS delay"), md.index("### Compass"))
        self.assertTrue(md.endswith("Compass barely moves with battery current: Z.\n"))

    def test_flag_is_marked(self):
        md = checkup.render(report(FLAG))
        self.assertIn("### Compass\n\n**Flag:** Compass tracks battery current: X.\n", md)
        self.assertNotIn("**Flag:**", checkup.render(report(NOTE)))

    def test_old_rows_have_no_section(self):
        md = checkup.render(report())
        self.assertNotIn("Compass", md)

    def test_removing_the_section_gives_the_037_text(self):
        # build.py compares reports with 0.3.7's this way
        import re
        with_c = checkup.render(report(FLAG))
        without = checkup.render(report())
        self.assertEqual(re.sub(r"\n### Compass\n\n[^\n]*\n", "", with_c), without)


class Flagged(unittest.TestCase):
    def test_strong_coupling_is_a_flag(self):
        self.assertTrue(checkup.flagged(report(FLAG)))

    def test_note_ok_and_missing_are_not(self):
        for c in (NOTE, OK, {"level": "not checked", "text": "Compass not checked: x."}, None):
            self.assertFalse(checkup.flagged(report(c)), c)


class RunCompass(unittest.TestCase):
    def test_missing_module(self):
        with mock.patch.dict(sys.modules, {"compass": None}):
            c = checkup.run_compass("a.ulg")
        self.assertEqual(c["level"], "not checked")
        self.assertIn("compass.py not found", c["text"])

    def test_module_result_is_passed_through(self):
        fake = types.SimpleNamespace(__version__="0.1.0", check=lambda p: dict(OK, log_seen=p))
        with mock.patch.dict(sys.modules, {"compass": fake}):
            c = checkup.run_compass("a.ulg")
        self.assertEqual((c["level"], c["log_seen"]), ("ok", "a.ulg"))

    def test_an_error_is_reported_not_raised(self):
        def boom(p):
            raise ValueError("no battery_status")
        fake = types.SimpleNamespace(__version__="0.1.0", check=boom)
        with mock.patch.dict(sys.modules, {"compass": fake}):
            c = checkup.run_compass("a.ulg")
        self.assertEqual(c["level"], "not checked")
        self.assertEqual(c["text"], "Compass not checked: ValueError: no battery_status.")

    def test_check_carries_it_into_the_row(self):
        with mock.patch.object(checkup, "run_health", return_value=dict(QUIET)), \
                mock.patch.object(checkup, "run_gps", return_value={"skipped": "x"}), \
                mock.patch.object(checkup, "read_delay_inputs", return_value=None), \
                mock.patch.object(checkup, "run_compass", return_value=FLAG):
            r = checkup.check("a.ulg", None)
        self.assertEqual(r["compass"]["level"], "flag")
        json.dumps(r)

    def test_real_module_on_a_missing_file(self):
        c = checkup.run_compass("/nonexistent/flight.ulg")
        self.assertEqual(c["level"], "not checked")
        self.assertTrue(c["text"].startswith("Compass not checked: "))


if __name__ == "__main__":
    unittest.main()

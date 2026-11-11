"""Tests for checkup 0.3.9: the battery check (battery_r.py, from the pre-registered battery-r-2026
census) joins the report as a last section after the compass; a collapse is a note, never a flag.

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""
import json
import re
import sys
import types
import unittest
from unittest import mock

import checkup

QUIET = {"health_ok": False, "health_reason": "under 60 s in the air"}
COMPASS_OK = {"compass": "0.1.0", "level": "ok", "text": "Compass barely moves with battery current: Z.",
              "coupling_pct": 1.0, "class": "weak", "fleet_place": "p25-p50", "cal_mag_comp_typ": 0}
NOTE = {"battery_r": "0.1.0", "level": "note", "text": "PX4's battery-resistance estimate was 0: N.",
        "collapsed": True, "r_end_mohm": 0.0, "bat_r_internal": -1.0}
OK = dict(NOTE, level="ok", text="PX4's battery-resistance estimate did not collapse: K.", collapsed=False,
          r_end_mohm=2.5)


def report(compass=None, battery=None):
    r = {"log": "/logs/a.ulg", "checkup": checkup.__version__, "health": dict(QUIET),
         "gps": {"skipped": "gps_lag.py and innov.py not found (run fetch_deps.py)"}, "gps_verdict": None}
    if compass is not None:
        r["compass"] = compass
    if battery is not None:
        r["battery"] = battery
    return r


class Version(unittest.TestCase):
    def test_version(self):
        self.assertEqual(checkup.__version__, "0.3.10")


class Render(unittest.TestCase):
    def test_section_comes_after_the_compass(self):
        md = checkup.render(report(COMPASS_OK, OK))
        self.assertIn("### Battery\n\nPX4's battery-resistance estimate did not collapse: K.\n", md)
        self.assertLess(md.index("### Compass"), md.index("### Battery"))
        self.assertTrue(md.endswith("did not collapse: K.\n"))

    def test_note_is_marked(self):
        md = checkup.render(report(COMPASS_OK, NOTE))
        self.assertIn("### Battery\n\n**Note:** PX4's battery-resistance estimate was 0: N.\n", md)
        self.assertNotIn("**Note:**", checkup.render(report(COMPASS_OK, OK)))

    def test_old_rows_have_no_section(self):
        self.assertNotIn("### Battery", checkup.render(report(COMPASS_OK)))

    def test_removing_the_section_gives_the_038_text(self):
        # build.py compares reports with 0.3.8's this way
        with_b = checkup.render(report(COMPASS_OK, NOTE))
        without = checkup.render(report(COMPASS_OK))
        self.assertEqual(re.sub(r"\n### Battery\n\n[^\n]*\n", "", with_b), without)


class Flagged(unittest.TestCase):
    def test_a_battery_note_is_not_a_flag(self):
        for b in (NOTE, OK, {"level": "not checked", "text": "Battery resistance estimate not checked: x."}, None):
            self.assertFalse(checkup.flagged(report(COMPASS_OK, b)), b)


class RunBattery(unittest.TestCase):
    def test_missing_module(self):
        with mock.patch.dict(sys.modules, {"battery_r": None}):
            b = checkup.run_battery("a.ulg")
        self.assertEqual(b["level"], "not checked")
        self.assertIn("battery_r.py not found", b["text"])

    def test_module_result_is_passed_through(self):
        fake = types.SimpleNamespace(__version__="0.1.0", check=lambda p: dict(OK, log_seen=p))
        with mock.patch.dict(sys.modules, {"battery_r": fake}):
            b = checkup.run_battery("a.ulg")
        self.assertEqual((b["level"], b["log_seen"]), ("ok", "a.ulg"))

    def test_an_error_is_reported_not_raised(self):
        def boom(p):
            raise ValueError("bad log")
        fake = types.SimpleNamespace(__version__="0.1.0", check=boom)
        with mock.patch.dict(sys.modules, {"battery_r": fake}):
            b = checkup.run_battery("a.ulg")
        self.assertEqual(b["level"], "not checked")
        self.assertEqual(b["text"], "Battery resistance estimate not checked: ValueError: bad log.")

    def test_check_carries_it_into_the_row(self):
        with mock.patch.object(checkup, "run_health", return_value=dict(QUIET)), \
                mock.patch.object(checkup, "run_gps", return_value={"skipped": "x"}), \
                mock.patch.object(checkup, "read_delay_inputs", return_value=None), \
                mock.patch.object(checkup, "run_compass", return_value=COMPASS_OK), \
                mock.patch.object(checkup, "run_battery", return_value=NOTE):
            r = checkup.check("a.ulg", None)
        self.assertEqual(r["battery"]["level"], "note")
        self.assertEqual(r["compass"]["level"], "ok")
        json.dumps(r)

    def test_real_module_on_a_missing_file(self):
        b = checkup.run_battery("/nonexistent/flight.ulg")
        self.assertEqual(b["level"], "not checked")
        self.assertTrue(b["text"].startswith("Battery resistance estimate not checked: "))


if __name__ == "__main__":
    unittest.main()

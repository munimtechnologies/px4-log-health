"""Synthetic tests for battery_r.py 0.1.1: a collapse note says how to log a flight that can show
why (studies/battery-r-2026/GRADE-replay.md). No flight log, no network.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import re
import unittest
from unittest import mock

import numpy as np

import battery_r as br

DOCS = "https://docs.px4.io/main/en/config/battery"
LEAD = ("In flight the estimate barely updates, and on 90 of 211 public PX4 flights it had already settled at "
        "the first logged sample (studies/battery-r-2026/GRADE-replay.md), so a log that starts at arming may "
        "not show why. ")
FULL = ("add the line `battery_status 0` to etc/logging/logger_topics.txt on the SD card (PX4 then logs that "
        "topic at full rate, on top of its default topics)")
STRIP = re.compile(r"(can also cause it\. ).*?(See PX4's battery estimation guide: )")


def measured(collapsed, r_end=0.0, first_zero_s=None):
    return {"read": "ok", "measurable": True, "collapsed": collapsed, "r_end_mohm": r_end,
            "first_zero_s": first_zero_s}


def old_note(when, setting):
    """battery_r 0.1.0's note text, as published in studies/v039."""
    return ("PX4's battery-resistance estimate was 0 for the whole second half of the flight (%s) while %s, so "
            "PX4 did not correct the voltage-based state of charge for the voltage drop under load: it reads low "
            "under load and recovers after landing. On public PX4 flights this happened on 9 of 53 that use the "
            "estimate. A wrong current sensor scale or offset can also cause it. See PX4's battery estimation "
            "guide: %s" % (when, setting, DOCS))


class Version(unittest.TestCase):
    def test_version(self):
        self.assertEqual(br.__version__, "0.1.1")
        self.assertEqual((br.REPLAY["n"], br.REPLAY["settled_at_start"]), (211, 90))


class LogMode(unittest.TestCase):
    def test_log_mode(self):
        self.assertEqual(br.log_mode({"SDLOG_MODE": 2}), 2)
        self.assertEqual(br.log_mode({"SDLOG_MODE": 0}), 0)
        self.assertEqual(br.log_mode({"SDLOG_MODE": 1.0}), 1)
        self.assertIsNone(br.log_mode({}))
        self.assertIsNone(br.log_mode(None))
        self.assertIsNone(br.log_mode({"SDLOG_MODE": "x"}))


class Hint(unittest.TestCase):
    def test_at_takeoff_from_arming(self):
        for sdlog in (0, None, 3, 4):
            self.assertEqual(br.why_hint(0.0, sdlog),
                             LEAD + "To see why, log one flight from boot (SDLOG_MODE 2) and " + FULL + ".")

    def test_in_flight_from_arming(self):
        self.assertEqual(br.why_hint(6.4, 0), "To see why, log one flight from boot (SDLOG_MODE 2) and " + FULL + ".")

    def test_from_boot(self):
        for fz in (0.0, 6.4):
            self.assertEqual(br.why_hint(fz, 2),
                             "To see why, " + FULL + "; this log already starts at boot (SDLOG_MODE 2).")
        self.assertTrue(br.why_hint(0.0, 1).endswith("(SDLOG_MODE 1)."))

    def test_plain_ascii_one_line(self):
        for fz in (0.0, 6.4):
            for sdlog in (0, 2, None):
                h = br.why_hint(fz, sdlog)
                self.assertNotIn("\n", h)
                h.encode("ascii")


class Verdict(unittest.TestCase):
    def test_note_carries_the_hint_before_the_docs_link(self):
        level, text = br.verdict(measured(True, 0.0, 0.0), -1.0, 0)
        self.assertEqual(level, "note")
        self.assertIn("can also cause it. " + LEAD + "To see why, log one flight from boot", text)
        self.assertIn("on top of its default topics). See PX4's battery estimation guide: ", text)
        self.assertTrue(text.endswith(DOCS))

    def test_removing_the_hint_gives_the_010_text(self):
        # build.py compares reports with studies/v039 this way
        cases = ((measured(True, 0.0, 0.0), -1.0, 0, "it was already 0 when the vehicle took off",
                  "BAT1_R_INTERNAL is -1 (use the estimate)"),
                 (measured(True, 0.0, 6.4), -1.0, 2, "it first reached 0 6.4 s after takeoff",
                  "BAT1_R_INTERNAL is -1 (use the estimate)"),
                 (measured(True, 0.0, 0.0), None, None, "it was already 0 when the vehicle took off",
                  "BAT1_R_INTERNAL is not in the log (PX4's default, -1, uses the estimate)"))
        for m, rint, sdlog, when, setting in cases:
            level, text = br.verdict(m, rint, sdlog)
            self.assertEqual(STRIP.sub(r"\1\2", text), old_note(when, setting))

    def test_default_sdlog_is_from_arming(self):
        self.assertEqual(br.verdict(measured(True, 0.0, 0.0), -1.0), br.verdict(measured(True, 0.0, 0.0), -1.0, 0))

    def test_no_hint_when_not_a_note(self):
        for m, rint in ((measured(True, 0.0, 0.0), 0.005), (measured(False, 4.2), -1.0), (measured(False, 4.2), None)):
            level, text = br.verdict(m, rint, 0)
            self.assertEqual(level, "ok")
            self.assertNotIn("To see why", text)
            self.assertEqual(br.verdict(m, rint), (level, text))


class Check(unittest.TestCase):
    def test_check_reads_sdlog_mode(self):
        n = 300
        t = np.arange(n, dtype=np.int64) * 200000
        cur = np.array([5.0 if i % 2 == 0 else 15.0 for i in range(n)])
        b = {"timestamp": t, "internal_resistance_estimate": np.zeros(n), "current_a": cur,
             "estimation_covariance_norm": np.linspace(1.0, 0.1, n), "cell_count": np.full(n, 4),
             "voltage_v": 16.0 - 0.04 * cur}
        data = {"battery_status": {0: b},
                "vehicle_land_detected": {0: {"timestamp": np.array([0], dtype=np.int64), "landed": np.array([0])}}}
        for params, sdlog, lead in (({"BAT1_R_INTERNAL": -1.0, "SDLOG_MODE": 1}, 1, False),
                                    ({"BAT1_R_INTERNAL": -1.0, "SDLOG_MODE": 0}, 0, True),
                                    ({"BAT1_R_INTERNAL": -1.0}, None, True)):
            with mock.patch.object(br, "read_log", return_value=(data, params)):
                r = br.check("a.ulg")
            self.assertEqual((r["level"], r["sdlog_mode"], r["battery_r"]), ("note", sdlog, "0.1.1"))
            self.assertEqual(LEAD in r["text"], lead)
            self.assertIn("To see why", r["text"])


if __name__ == "__main__":
    unittest.main()

"""Tests for checkup.py. By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

import checkup


def lag(d, passed=True, lag_ms=None, param=110.0):
    return {"passed": passed, "reason": "ok" if passed else "too little manoeuvring",
            "lag_ms": (param or 110.0) + d if lag_ms is None else lag_ms, "se_ms": 1.2,
            "param_gps_delay_ms": param, "lag_minus_param_ms": None if param is None else d}


def inn(x, passed=True):
    return {"passed": passed, "reason": "ok" if passed else "too few in-flight innovations",
            "implied_excess_ms": x if passed else None}


HEALTH = {"health_ok": True, "health_reason": "ok", "release": "v1.15.4", "flight_s": 420.0,
          "multicopter": True, "vib_median": 12.2, "vib_p95": 18.0, "vib_units": "m/s^2", "vib_band": "red",
          "clip_count": 3, "clip_per_min": 0.43, "jam_median": 35.0, "jam_state_max": 2, "sats_median": 14.0,
          "eph_median": 0.6, "battery_warning_max": 1, "failsafe_in_flight": False, "sat_share": 0.012,
          "flags": ["vibration red (median 12.2 m/s^2; Flight Review's plot turns orange at 4.9 and red at 9.8)",
                    "accelerometer clipped 3 times in flight", "battery warning level 1 in flight"],
          "notes": ["GPS receiver reported interference (jamming state 2: visible, fix kept)"]}


class Verdict(unittest.TestCase):
    def test_not_measured(self):
        v = checkup.gps_verdict(lag(80, passed=False), None)
        self.assertEqual(v["status"], "not measured")
        self.assertEqual(v["reason"], "too little manoeuvring")
        self.assertEqual(checkup.gps_verdict(None, None)["status"], "not measured")

    def test_within_50_ms_is_ok_and_suggests_nothing(self):
        for d in (-50.0, 0.0, 12.0, 50.0):
            v = checkup.gps_verdict(lag(d), inn(5.0))
            self.assertEqual(v["status"], "ok", d)
            self.assertIsNone(v["suggest_ms"])

    def test_near_the_line(self):
        for d, near in ((-50.0, True), (-40.0, True), (-39.9, False), (0.0, False), (49.7, True)):
            self.assertEqual(checkup.gps_verdict(lag(d), inn(5.0))["near_line"], near, d)
        self.assertFalse(checkup.gps_verdict(lag(64.6), inn(31.0))["near_line"])
        self.assertFalse(checkup.gps_verdict(lag(0.0, lag_ms=155.0, param=None), None)["near_line"])

    def test_off_and_agreeing_suggests_the_measured_lag(self):
        v = checkup.gps_verdict(lag(64.6), inn(31.0))
        self.assertEqual((v["status"], v["innov"], v["suggest_ms"]), ("off", "agrees", 175))
        v = checkup.gps_verdict(lag(-60.0), inn(-20.0))
        self.assertEqual((v["status"], v["innov"], v["suggest_ms"]), ("off", "agrees", 50))

    def test_off_and_disagreeing_suggests_nothing(self):
        v = checkup.gps_verdict(lag(90.0), inn(-15.0))
        self.assertEqual((v["status"], v["innov"], v["suggest_ms"]), ("off", "disagrees", None))

    def test_off_without_an_innovation_check_still_suggests(self):
        for i in (None, inn(None, passed=False)):
            v = checkup.gps_verdict(lag(120.0), i)
            self.assertEqual((v["status"], v["innov"], v["suggest_ms"]), ("off", "not checked", 230))

    def test_no_setting_in_the_log(self):
        v = checkup.gps_verdict(lag(0.0, lag_ms=140.0, param=None), inn(10.0))
        self.assertEqual((v["status"], v["suggest_ms"], v["innov"]), ("no setting", None, "not checked"))


def report(h=HEALTH, lag_row=None, inn_row=None, skipped=None, place=None):
    h = dict(h)
    if place is not None:
        h["fleet_place"] = place
    g = {"lag": lag_row, "innov": inn_row}
    if skipped:
        g = {"lag": None, "innov": None, "skipped": skipped}
    return {"log": "/logs/flight.ulg", "checkup": checkup.__version__, "health": h, "gps": g,
            "gps_verdict": None if skipped else checkup.gps_verdict(lag_row, inn_row)}


class Render(unittest.TestCase):
    def test_flags_notes_and_table(self):
        md = checkup.render(report(lag_row=lag(10.0), inn_row=inn(3.0),
                                   place={"vib_median": "p75-p90", "clip_per_min": "p75-p90"}))
        self.assertIn("## flight.ulg", md)
        self.assertIn("PX4 v1.15.4, 7.0 min in the air, multicopter.", md)
        self.assertIn("**3 flags**", md)
        self.assertIn("- accelerometer clipped 3 times in flight", md)
        self.assertIn("Notes:", md)
        self.assertIn("| Vibration, median | 12.2 m/s² (red) | p75-p90 |", md)
        self.assertIn("| Battery warning, worst | low |  |", md)
        self.assertIn("| Failsafe in flight | no |  |", md)
        self.assertIn("| A motor at full command | 1.2% of flight |", md)
        self.assertIn("Within 50 ms of the setting: nothing to change.", md)
        self.assertIn("this vehicle's EKF2_GPS_DELAY is 110 ms (+10 ms).", md)
        self.assertNotIn("Suggestion", md)
        self.assertNotIn("*(tied)*", md)

    def test_tied_place_is_explained(self):
        md = checkup.render(report(lag_row=lag(0.0), place={"clip_per_min": "p10-p75 (tied)",
                                                             "vib_median": "p75-p90"}))
        self.assertIn("| Accelerometer clipping | 3 events (0.43 per min) | p10-p75 (tied) |", md)
        self.assertIn("*(tied)*: many public flights have exactly this value", md)
        self.assertLess(md.index("*(tied)*"), md.index("### GPS delay"))

    def test_close_to_the_line_says_so_with_one_decimal(self):
        md = checkup.render(report(lag_row=lag(49.7), inn_row=inn(23.0)))
        self.assertIn("The GPS lags the IMU by **159.7 ms** (± 1.2); this vehicle's EKF2_GPS_DELAY is "
                      "110 ms (+49.7 ms).", md)
        self.assertIn("close to the 50 ms line (+49.7 ms): no change suggested; measure another flight", md)
        self.assertNotIn("nothing to change", md)
        self.assertNotIn("Suggestion", md)
        md = checkup.render(report(lag_row=lag(-42.0), inn_row=inn(-9.0)))
        self.assertIn("(-42.0 ms): no change suggested", md)

    def test_no_flags(self):
        h = dict(HEALTH, flags=[], notes=[])
        md = checkup.render(report(h, lag_row=lag(0.0), inn_row=None))
        self.assertIn("**No flags.**", md)
        self.assertNotIn("Notes:", md)

    def test_old_release_vibration_is_not_banded(self):
        h = dict(HEALTH, release="v1.11.3", vib_units="m/s", vib_band=None, vib_median=0.012, flags=[])
        md = checkup.render(report(h, place={"vib_median": "below p10"}, lag_row=lag(0.0)))
        self.assertIn("0.012 m/s (PX4 v1.12 or older units; not banded)", md)
        self.assertNotIn("below p10", md)
        self.assertNotIn("95th percentile", md)

    def test_suggestion_when_off_and_agreeing(self):
        md = checkup.render(report(lag_row=lag(64.6), inn_row=inn(31.0)))
        self.assertIn("The GPS lags the IMU by **175 ms** (± 1.2); this vehicle's EKF2_GPS_DELAY is 110 ms (+65 ms).", md)
        self.assertIn("lean the same way (implied +31 ms)", md)
        self.assertIn("**Suggestion:** try EKF2_GPS_DELAY = 175 as a starting value.", md)
        self.assertIn("EKF2 log replay", md)

    def test_no_suggestion_when_disagreeing(self):
        md = checkup.render(report(lag_row=lag(90.0), inn_row=inn(-15.0)))
        self.assertIn("lean the other way (implied -15 ms)", md)
        self.assertNotIn("Suggestion", md)
        self.assertIn("there is no suggestion", md)

    def test_innovation_not_checked_says_why(self):
        md = checkup.render(report(lag_row=lag(120.0), inn_row=inn(None, passed=False)))
        self.assertIn("could not be checked (too few in-flight innovations)", md)
        self.assertIn("EKF2_GPS_DELAY = 230", md)

    def test_lag_not_measured_and_gps_skipped(self):
        md = checkup.render(report(lag_row=lag(0.0, passed=False)))
        self.assertIn("Not measured: too little manoeuvring.", md)
        md = checkup.render(report(skipped="gps_lag.py and innov.py not found (run fetch_deps.py)"))
        self.assertIn("Not checked: gps_lag.py and innov.py not found", md)

    def test_unreadable_log(self):
        r = report({"health_ok": False, "health_reason": "under 60 s in the air"}, lag_row=lag(0.0, passed=False))
        md = checkup.render(r)
        self.assertIn("Health not read: under 60 s in the air.", md)
        self.assertIn("### GPS delay", md)


class Main(unittest.TestCase):
    def run_main(self, healths, gpss, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            md, js = os.path.join(tmp, "summary.md"), os.path.join(tmp, "rows.jsonl")
            with open(md, "w", encoding="utf-8") as f:
                f.write("earlier step\n")
            with mock.patch.object(checkup, "run_health", side_effect=healths), \
                    mock.patch.object(checkup, "run_gps", side_effect=gpss):
                code = checkup.main(["a.ulg", "b.ulg", "--md", md, "--json", js,
                                     "--baseline", os.path.join(tmp, "none.json")] + list(extra))
            with open(md, encoding="utf-8") as f:
                text = f.read()
            with open(js, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f]
        return code, text, rows

    def test_appends_markdown_and_writes_json(self):
        quiet = dict(HEALTH, flags=[], notes=[])
        g = {"lag": dict(lag(5.0), bias_ne_m_s2=np.array([0.01, -0.02]), r2=np.float64(0.99)), "innov": inn(2.0)}
        code, text, rows = self.run_main([quiet, quiet], [g, g])
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("earlier step\n# PX4 log health"))
        self.assertIn("2 logs, checked with px4-log-health checkup %s by MV2 of Munim, Inc." % checkup.__version__, text)
        self.assertIn("## a.ulg", text)
        self.assertIn("## b.ulg", text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["gps"]["lag"]["bias_ne_m_s2"], [0.01, -0.02])
        self.assertEqual(rows[0]["gps_verdict"]["status"], "ok")
        self.assertFalse(rows[0]["gps_verdict"]["near_line"])

    def test_fail_on_flag(self):
        quiet = dict(HEALTH, flags=[], notes=[])
        ok = {"lag": lag(5.0), "innov": inn(2.0)}
        near = {"lag": lag(45.0), "innov": inn(20.0)}
        off = {"lag": lag(80.0), "innov": inn(20.0)}
        self.assertEqual(self.run_main([quiet, quiet], [ok, ok], "--fail-on-flag")[0], 0)
        self.assertEqual(self.run_main([quiet, quiet], [ok, near], "--fail-on-flag")[0], 0)
        self.assertEqual(self.run_main([quiet, HEALTH], [ok, ok], "--fail-on-flag")[0], 1)
        self.assertEqual(self.run_main([quiet, quiet], [ok, off], "--fail-on-flag")[0], 1)
        self.assertEqual(self.run_main([quiet, HEALTH], [ok, off])[0], 0)

    def test_a_crashing_log_is_reported_not_raised(self):
        g = {"lag": lag(0.0, passed=False), "innov": None}
        code, text, rows = self.run_main([ValueError("bad file"), dict(HEALTH, flags=[])], [g, g])
        self.assertEqual(code, 0)
        self.assertIn("Health not read: error: bad file.", text)
        self.assertFalse(rows[0]["health"]["health_ok"])


class Gps(unittest.TestCase):
    def test_missing_px4_gps_lag_is_skipped(self):
        real_import = __import__

        def no_gps_lag(name, *args, **kwargs):
            if name in ("gps_lag", "innov"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)
        with mock.patch("builtins.__import__", side_effect=no_gps_lag):
            g = checkup.run_gps("missing.ulg")
        self.assertIn("not found", g["skipped"])


if __name__ == "__main__":
    unittest.main()

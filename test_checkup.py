"""Tests for checkup.py. By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

import checkup

NOTE = "median lift 0.43 vs 0.24"   # in checkup.MISMATCH_NOTE, shown only for logs more than 50 ms off


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

# PX4 v1.18 parameters (PR #26660) with no receiver ids set, and a receiver's sample times
P18 = {"SENS_GPS0_DELAY": 110, "SENS_GPS1_DELAY": 110, "SENS_GPS0_ID": 0, "SENS_GPS1_ID": 0}
DEV = 11141433
T = [1000000 + 100000 * k for k in range(9)]


def inputs(params, delay_us=110000, dev=DEV, topic="vehicle_gps_position", samples=None):
    return {"params": params, "topic": topic, "stamps": list(T),
            "samples": [t - delay_us for t in T] if samples is None else samples,
            "device_ids": [dev] * len(T)}


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
        self.assertEqual(v["param_name"], "EKF2_GPS_DELAY")
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


class Applied(unittest.TestCase):
    """applied_delay: the delay EKF2 used in logs from PX4 v1.18 and newer."""

    def test_old_firmware_is_left_to_gps_lag(self):
        self.assertIsNone(checkup.applied_delay(**inputs({"EKF2_GPS_DELAY": 110, "SENS_GPS0_DELAY": 110})))
        self.assertIsNone(checkup.applied_delay(**inputs({"EKF2_GPS_DELAY": 110})))
        self.assertIsNone(checkup.applied_delay(**inputs({})))

    def test_v118_first_slot(self):
        a = checkup.applied_delay(**inputs(P18))
        self.assertEqual((a["applied_ms"], a["param"], a["param_ms"], a["source"], a["unmatched_id"]),
                         (110.0, "SENS_GPS0_DELAY", 110.0, "parameter", False))
        self.assertEqual((a["device_id"], a["prefix"]), (DEV, "SENS_GPS"))

    def test_no_ids_takes_the_slot_whose_delay_was_applied(self):
        a = checkup.applied_delay(**inputs(dict(P18, SENS_GPS1_DELAY=180), delay_us=180000))
        self.assertEqual((a["param"], a["param_ms"], a["source"]), ("SENS_GPS1_DELAY", 180.0, "parameter"))

    def test_slot_matched_by_device_id(self):
        p = dict(P18, SENS_GPS0_ID=5, SENS_GPS1_ID=DEV, SENS_GPS1_DELAY=180)
        a = checkup.applied_delay(**inputs(p, delay_us=180000))
        self.assertEqual((a["param"], a["param_ms"], a["source"], a["unmatched_id"]),
                         ("SENS_GPS1_DELAY", 180.0, "parameter", False))

    def test_a_negative_int32_id_matches_its_device(self):
        dev = 0xF0000001
        p = dict(P18, SENS_GPS0_ID=-268435455, SENS_GPS0_DELAY=90)
        a = checkup.applied_delay(**inputs(p, delay_us=90000, dev=dev))
        self.assertEqual((a["param"], a["param_ms"], a["source"]), ("SENS_GPS0_DELAY", 90.0, "parameter"))
        self.assertEqual(checkup._i32(dev), -268435455)
        self.assertEqual(checkup._i32(DEV), DEV)

    def test_ids_set_but_none_matching_means_the_110_ms_default(self):
        p = dict(P18, SENS_GPS0_ID=5, SENS_GPS0_DELAY=60)
        a = checkup.applied_delay(**inputs(p))
        self.assertEqual((a["param"], a["param_ms"], a["source"], a["unmatched_id"]),
                         (None, 110.0, "parameter", True))

    def test_pps_or_driver_timing_is_other(self):
        samples = [t - 95000 - 10 * k for k, t in enumerate(T)]      # 95.00 .. 95.08 ms
        a = checkup.applied_delay(**inputs(P18, samples=samples))
        self.assertEqual((a["applied_ms"], a["param"], a["source"]), (95.0, "SENS_GPS0_DELAY", "other"))

    def test_zero_delay(self):
        for samples in (list(T), [0] * len(T)):
            a = checkup.applied_delay(**inputs(dict(P18, SENS_GPS0_DELAY=0), samples=samples))
            self.assertEqual((a["applied_ms"], a["param_ms"], a["source"]), (0.0, 0.0, "parameter"))

    def test_px4_main_names(self):
        p = {"SENS_GNSS0_DELAY": 110, "SENS_GNSS1_DELAY": 110, "SENS_GNSS0_ID": 0, "SENS_GNSS1_ID": 0}
        a = checkup.applied_delay(**inputs(p, topic="vehicle_gnss"))
        self.assertEqual((a["prefix"], a["param"], a["topic"], a["source"]),
                         ("SENS_GNSS", "SENS_GNSS0_DELAY", "vehicle_gnss", "parameter"))

    def test_no_samples(self):
        a = checkup.applied_delay(P18, "vehicle_gps_position", [], [], [])
        self.assertIsNone(a["applied_ms"])
        v = checkup.gps_verdict(lag(0.0, lag_ms=140.0, param=None), None, a)
        self.assertEqual(v["status"], "no setting")


class Verdict118(unittest.TestCase):
    def test_off_suggests_the_receivers_parameter(self):
        a = checkup.applied_delay(**inputs(P18))
        v = checkup.gps_verdict(lag(0.0, lag_ms=175.0, param=None), inn(31.0), a)
        self.assertEqual((v["status"], v["d_ms"], v["innov"], v["suggest_ms"], v["param_name"]),
                         ("off", 65.0, "agrees", 175, "SENS_GPS0_DELAY"))
        md = checkup.render(report(lag_row=lag(0.0, lag_ms=175.0, param=None), inn_row=inn(31.0), applied=a))
        self.assertIn("The GPS lags the IMU by **175 ms** (± 1.2); this vehicle's SENS_GPS0_DELAY is 110 ms, "
                      "and EKF2 fused the GPS samples that much before they arrived (+65 ms).", md)
        self.assertIn("**Suggestion:** try SENS_GPS0_DELAY = 175 as a starting value.", md)
        self.assertIn("PX4 v1.18 replaced EKF2_GPS_DELAY", md)
        self.assertNotIn("try EKF2_GPS_DELAY", md)
        self.assertIn(NOTE, md)

    def test_within_50_ms(self):
        a = checkup.applied_delay(**inputs(P18))
        v = checkup.gps_verdict(lag(0.0, lag_ms=130.0, param=None), inn(5.0), a)
        self.assertEqual((v["status"], v["suggest_ms"], v["near_line"]), ("ok", None, False))
        md = checkup.render(report(lag_row=lag(0.0, lag_ms=130.0, param=None), inn_row=inn(5.0), applied=a))
        self.assertNotIn(NOTE, md)

    def test_pps_timing_gets_no_suggestion(self):
        a = checkup.applied_delay(**inputs(P18, samples=[t - 95000 for t in T]))
        row = lag(0.0, lag_ms=175.0, param=None)
        v = checkup.gps_verdict(row, inn(40.0), a)
        self.assertEqual((v["status"], v["suggest_ms"]), ("off", None))
        self.assertIn("PPS", v["no_suggestion"])
        md = checkup.render(report(lag_row=row, inn_row=inn(40.0), applied=a))
        self.assertIn("EKF2 fused this log's GPS samples 95 ms before they arrived, a shift set by PPS or the "
                      "GPS driver rather than SENS_GPSn_DELAY (+80 ms).", md)
        self.assertIn("so there is no parameter to suggest", md)
        self.assertNotIn("Suggestion", md)
        self.assertIn("20% of logs against 16%", md)

    def test_unmatched_id_suggests_an_id_and_a_delay(self):
        dev = 0xF0000001
        a = checkup.applied_delay(**inputs(dict(P18, SENS_GPS0_ID=5), dev=dev))
        row = lag(0.0, lag_ms=175.0, param=None)
        self.assertEqual(checkup.gps_verdict(row, inn(31.0), a)["suggest_ms"], 175)
        md = checkup.render(report(lag_row=row, inn_row=inn(31.0), applied=a))
        self.assertIn("this receiver (device id 4026531841) matches no SENS_GPSn_ID, so PX4 applied its "
                      "default 110 ms (+65 ms).", md)
        self.assertIn("try SENS_GPS0_ID = -268435455 and SENS_GPS0_DELAY = 175 as a starting value", md)

    def test_ekf2_gps_delay_comes_first(self):
        a = checkup.applied_delay(**inputs(P18))
        v = checkup.gps_verdict(lag(64.6), inn(31.0), a)
        self.assertEqual((v["param_name"], v["suggest_ms"]), ("EKF2_GPS_DELAY", 175))
        self.assertNotIn("applied", v)


class MismatchNote(unittest.TestCase):
    """The mismatch-2026 line (px4-gps-lag studies/mismatch-2026/GRADE.md, D1 and D2)."""

    def test_the_figures_match_the_study(self):
        # summary.json: median lift 0.4348 (mismatched) vs 0.24285; rejected share 0.2043 vs 0.1595; n 93 vs 163
        self.assertEqual((round(0.4348, 2), round(0.24285, 2)), (0.43, 0.24))
        self.assertEqual((round(10 ** 0.4348, 1), round(10 ** 0.24285, 1)), (2.7, 1.7))
        self.assertEqual((round(100 * 0.2043), round(100 * 0.1595)), (20, 16))
        for part in ("median lift 0.43 vs 0.24", "2.7 times", "1.7 times", "20% of logs against 16%",
                     "within chance on 93 and 163 logs", "studies/mismatch-2026/GRADE.md"):
            self.assertIn(part, checkup.MISMATCH_NOTE)

    def test_shown_after_the_advice_on_every_off_log(self):
        for d, i in ((64.6, inn(31.0)), (90.0, inn(-15.0)), (120.0, None), (-60.0, inn(-20.0))):
            md = checkup.render(report(lag_row=lag(d), inn_row=i))
            self.assertEqual(md.count(NOTE), 1, d)
            self.assertLess(md.index("ms off" if i and i["implied_excess_ms"] == -15.0 else "**Suggestion:**"),
                            md.index(NOTE), d)

    def test_not_shown_otherwise(self):
        for row in (lag(0.0), lag(49.7), lag(-50.0), lag(0.0, passed=False), lag(0.0, lag_ms=200.0, param=None)):
            self.assertNotIn(NOTE, checkup.render(report(lag_row=row, inn_row=inn(5.0))))
        self.assertNotIn(NOTE, checkup.render(report(skipped="gps_lag.py and innov.py not found")))


def report(h=HEALTH, lag_row=None, inn_row=None, skipped=None, place=None, applied=None):
    h = dict(h)
    if place is not None:
        h["fleet_place"] = place
    g = {"lag": lag_row, "innov": inn_row}
    if skipped:
        g = {"lag": None, "innov": None, "skipped": skipped}
    return {"log": "/logs/flight.ulg", "checkup": checkup.__version__, "health": h, "gps": g,
            "gps_verdict": None if skipped else checkup.gps_verdict(lag_row, inn_row, applied)}


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
        self.assertNotIn("PX4 v1.18 replaced", md)
        self.assertNotIn(NOTE, md)

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
        self.assertNotIn(NOTE, md)
        md = checkup.render(report(lag_row=lag(-42.0), inn_row=inn(-9.0)))
        self.assertIn("(-42.0 ms): no change suggested", md)

    def test_no_setting_names_both_parameters(self):
        md = checkup.render(report(lag_row=lag(0.0, lag_ms=140.0, param=None)))
        self.assertIn("no GPS delay setting to compare with (EKF2_GPS_DELAY, or SENS_GPS0_DELAY", md)
        self.assertNotIn(NOTE, md)

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
        self.assertIn(checkup.MISMATCH_NOTE, md)

    def test_no_suggestion_when_disagreeing(self):
        md = checkup.render(report(lag_row=lag(90.0), inn_row=inn(-15.0)))
        self.assertIn("lean the other way (implied -15 ms)", md)
        self.assertNotIn("Suggestion", md)
        self.assertIn("there is no suggestion", md)
        self.assertIn(NOTE, md)

    def test_innovation_not_checked_says_why(self):
        md = checkup.render(report(lag_row=lag(120.0), inn_row=inn(None, passed=False)))
        self.assertIn("could not be checked (too few in-flight innovations)", md)
        self.assertIn("EKF2_GPS_DELAY = 230", md)

    def test_lag_not_measured_and_gps_skipped(self):
        md = checkup.render(report(lag_row=lag(0.0, passed=False)))
        self.assertIn("Not measured: too little manoeuvring.", md)
        self.assertNotIn(NOTE, md)
        md = checkup.render(report(skipped="gps_lag.py and innov.py not found (run fetch_deps.py)"))
        self.assertIn("Not checked: gps_lag.py and innov.py not found", md)

    def test_unreadable_log(self):
        r = report({"health_ok": False, "health_reason": "under 60 s in the air"}, lag_row=lag(0.0, passed=False))
        md = checkup.render(r)
        self.assertIn("Health not read: under 60 s in the air.", md)
        self.assertIn("### GPS delay", md)


class Check(unittest.TestCase):
    QUIET = dict(HEALTH, flags=[], notes=[])

    def check(self, gps, delay_inputs=None, delay_error=None):
        kw = {"side_effect": delay_error} if delay_error else {"return_value": delay_inputs}
        with mock.patch.object(checkup, "run_health", return_value=dict(self.QUIET)), \
                mock.patch.object(checkup, "run_gps", return_value=gps), \
                mock.patch.object(checkup, "read_delay_inputs", **kw):
            return checkup.check("flight.ulg", None)

    def test_a_px4_main_log_says_why_the_lag_is_missing(self):
        r = self.check({"lag": {"passed": False, "reason": "error: no vehicle_gps_position"}, "innov": None},
                       inputs({"SENS_GNSS0_DELAY": 110}, topic="vehicle_gnss"))
        v = r["gps_verdict"]
        self.assertEqual(v["status"], "not measured")
        self.assertIn("vehicle_gnss", v["reason"])
        self.assertIn("(error: no vehicle_gps_position)", v["reason"])
        self.assertIn("Not measured: this log uses PX4's newer vehicle_gnss topic", checkup.render(r))

    def test_a_v118_log_is_compared_with_its_applied_delay(self):
        r = self.check({"lag": lag(0.0, lag_ms=175.0, param=None), "innov": inn(31.0)}, inputs(P18))
        v = r["gps_verdict"]
        self.assertEqual((v["status"], v["param_name"], v["suggest_ms"]), ("off", "SENS_GPS0_DELAY", 175))
        self.assertEqual(v["applied"]["applied_ms"], 110.0)
        json.dumps(r)

    def test_an_unreadable_log_falls_back_to_gps_lag(self):
        r = self.check({"lag": lag(64.6), "innov": inn(31.0)}, delay_error=OSError("cannot open"))
        self.assertEqual((r["gps_verdict"]["param_name"], r["gps_verdict"]["suggest_ms"]), ("EKF2_GPS_DELAY", 175))

    def test_skipped_gps_has_no_verdict(self):
        r = self.check({"lag": None, "innov": None, "skipped": "gps_lag.py and innov.py not found"}, inputs(P18))
        self.assertIsNone(r["gps_verdict"])


class ReadInputs(unittest.TestCase):
    @staticmethod
    def fake_ulog(name, data, params):
        class Data:
            pass
        d = Data()
        d.name, d.multi_id, d.data = name, 0, data

        class FakeULog:
            def __init__(self, path, topics, flag):
                self.data_list = [d]
                self.initial_parameters = params
        return FakeULog

    def test_vehicle_gnss_uses_the_receivers_stamps(self):
        t = np.array(T, dtype=np.uint64)
        data = {"timestamp": t + 300, "timestamp_sample": t - 110000, "receiver.timestamp": t,
                "receiver.timestamp_sample": t - 110000, "receiver.device_id": np.full(9, DEV, dtype=np.uint32)}
        with mock.patch("pyulog.ULog", self.fake_ulog("vehicle_gnss", data, {"SENS_GNSS0_DELAY": 110})):
            got = checkup.read_delay_inputs("x.ulg")
        self.assertEqual((got["topic"], got["stamps"], got["device_ids"][0]), ("vehicle_gnss", T, DEV))
        a = checkup.applied_delay(**got)
        self.assertEqual((a["applied_ms"], a["param"], a["source"]), (110.0, "SENS_GNSS0_DELAY", "parameter"))

    def test_vehicle_gps_position(self):
        t = np.array(T, dtype=np.uint64)
        data = {"timestamp": t, "timestamp_sample": t, "device_id": np.full(9, DEV, dtype=np.uint32)}
        with mock.patch("pyulog.ULog", self.fake_ulog("vehicle_gps_position", data, {"EKF2_GPS_DELAY": 110.0})):
            got = checkup.read_delay_inputs("x.ulg")
        self.assertEqual((got["topic"], got["samples"]), ("vehicle_gps_position", T))
        self.assertIsNone(checkup.applied_delay(**got))


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

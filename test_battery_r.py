"""Synthetic tests for battery_r.py (no flight log, no network).
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import contextlib
import io
import json
import unittest

import numpy as np

import battery_r as br


def battery(est, cur, volt=None, cov=None, cells=4, n=None):
    n = len(est) if n is None else n
    t = np.arange(n, dtype=np.int64) * 200000          # 5 Hz
    b = {"timestamp": t, "internal_resistance_estimate": np.asarray(est, dtype=np.float64),
         "current_a": np.asarray(cur, dtype=np.float64),
         "estimation_covariance_norm": (np.linspace(1.0, 0.1, n) if cov is None else np.asarray(cov)),
         "cell_count": np.full(n, cells)}
    if volt is not None:
        b["voltage_v"] = np.asarray(volt, dtype=np.float64)
    return b


def flight(b, landed=0):
    return {"battery_status": {0: b},
            "vehicle_land_detected": {0: {"timestamp": np.array([0], dtype=np.int64),
                                          "landed": np.array([landed])}}}


def steps(n):
    return np.array([5.0 if i % 2 == 0 else 15.0 for i in range(n)])


def measured(collapsed, r_end=0.0, first_zero_s=None):
    return {"read": "ok", "measurable": True, "collapsed": collapsed, "r_end_mohm": r_end,
            "first_zero_s": first_zero_s}


class Measure(unittest.TestCase):
    """The census measurement, copied unchanged: same results as test_battr_logs.py's cases."""

    def test_collapse(self):
        est = [0.005] * 100 + [0.0] * 200
        cur = steps(300)
        m = br.measure_data(flight(battery(est, cur, volt=16.0 - 0.04 * cur)))
        self.assertTrue(m["measurable"])
        self.assertTrue(m["collapsed"])
        self.assertEqual(m["zero_share"], 0.6667)
        self.assertEqual(m["first_zero_s"], 20.0)
        self.assertEqual(m["last_change_frac"], 0.3344)
        self.assertEqual((m["r_start_mohm"], m["r_end_mohm"], m["changes"]), (5.0, 0.0, 1))
        self.assertTrue(m["step_valid"])
        self.assertAlmostEqual(m["step"]["mohm_per_cell"], 10.0, places=3)

    def test_not_measurable(self):
        m = br.measure_data(flight(battery([0.005] * 300, [0.2] * 300)))
        self.assertEqual(m["why_not"], "median airborne current under 0.5 A")
        m = br.measure_data(flight(battery([0.005] * 300, steps(300)), landed=1))
        self.assertEqual(m["why_not"], "fewer than 150 airborne samples")
        m = br.measure_data(flight(battery([0.0] * 300, steps(300), cov=np.zeros(300))))
        self.assertEqual(m["why_not"], "no active estimator with valid current")
        self.assertEqual(br.measure_data({})["read"], "no battery_status data")


class RInternal(unittest.TestCase):
    def test_r_internal(self):
        self.assertEqual(br.r_internal({"BAT1_R_INTERNAL": -1.0}), -1.0)
        self.assertEqual(br.r_internal({"BAT_R_INTERNAL": 0.004}), 0.004)
        self.assertIsNone(br.r_internal({}))
        self.assertIsNone(br.r_internal(None))
        self.assertIsNone(br.r_internal({"BAT1_R_INTERNAL": "x"}))


class Verdict(unittest.TestCase):
    def test_collapse_using_the_estimate_is_a_note(self):
        level, text = br.verdict(measured(True, 0.0, 0.0), -1.0)
        self.assertEqual(level, "note")
        self.assertIn("(it was already 0 when the vehicle took off) while BAT1_R_INTERNAL is -1", text)
        self.assertIn("did not correct the voltage-based state of charge", text)
        self.assertIn("this happened on 9 of 53 that use the estimate", text)
        self.assertTrue(text.endswith("https://docs.px4.io/main/en/config/battery"))
        self.assertNotIn("\n", text)

    def test_collapse_in_flight(self):
        level, text = br.verdict(measured(True, 0.0, 15.2), -1.0)
        self.assertEqual(level, "note")
        self.assertIn("(it first reached 0 15.2 s after takeoff)", text)

    def test_collapse_without_the_parameter(self):
        level, text = br.verdict(measured(True, 0.0, 0.0), None)
        self.assertEqual(level, "note")
        self.assertIn("BAT1_R_INTERNAL is not in the log", text)

    def test_collapse_with_a_set_value_is_ok(self):
        level, text = br.verdict(measured(True, 0.0, 0.0), 0.005)
        self.assertEqual(level, "ok")
        self.assertIn("BAT1_R_INTERNAL is set (5.0 mOhm per cell), so PX4 used that value", text)

    def test_alive(self):
        level, text = br.verdict(measured(False, 4.2), -1.0)
        self.assertEqual(level, "ok")
        self.assertEqual(text, "PX4's battery-resistance estimate did not collapse: it ended the flight at "
                               "4.2 mOhm per cell (public PX4 flights: median 2.7, p10-p90 1.2-7.5).")
        level, text = br.verdict(measured(False, 4.2), 0.002)
        self.assertTrue(text.endswith(" BAT1_R_INTERNAL is set, so PX4 used that value instead."))

    def test_not_checked(self):
        self.assertEqual(br.verdict({"read": "no battery_status data"}),
                         ("not checked", "Battery resistance estimate not checked: no battery_status data."))
        self.assertEqual(br.verdict({"read": "ok", "measurable": False,
                                     "why_not": "median airborne current under 0.5 A"}),
                         ("not checked", "Battery resistance estimate not checked: median airborne current "
                                         "under 0.5 A."))

    def test_measured_collapse_end_to_end(self):
        est = [0.005] * 100 + [0.0] * 200
        m = br.measure_data(flight(battery(est, steps(300))))
        level, text = br.verdict(m, -1.0)
        self.assertEqual(level, "note")
        self.assertIn("(it first reached 0 20.0 s after takeoff)", text)


class Cli(unittest.TestCase):
    def test_missing_file_is_reported(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = br.main(["/nonexistent/flight.ulg", "--json"])
        self.assertEqual(rc, 0)
        rows = json.loads(buf.getvalue())
        self.assertEqual(rows[0]["level"], "not checked")
        self.assertTrue(rows[0]["text"].startswith("Battery resistance estimate not checked: "))


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name.
"""Tests for fwmatch.py (stdlib unittest). Numeric expectations were worked out by hand first."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fwmatch as fm  # noqa: E402

N = 4


class TestConstants(unittest.TestCase):
    def test_settings_match_sources(self):
        fw = fm.SETTINGS["firmware"]
        tool = fm.SETTINGS["replay_tool"]
        self.assertEqual((fw["lam"], fw["ocv_cov"], fw["r_cov"]), (0.95, 1.5, 0.1))
        self.assertEqual((tool["lam"], tool["ocv_cov"], tool["r_cov"]), (0.99, 1.2, 0.1))
        self.assertTrue(fw["skip_low_current"])
        self.assertFalse(tool["skip_low_current"])
        self.assertFalse(fw["accept_ties"])
        self.assertTrue(tool["accept_ties"])

    def test_initial_norms(self):
        # firmware: sqrt(6^2 + 0.4^2) = sqrt(36.16) = 6.0133186; PX4 logs 6.01331854 for a 4S pack.
        self.assertAlmostEqual(fm.initial_norm(N, "firmware"), 6.01331854, delta=1e-5)
        # tool: sqrt(4.8^2 + 0.4^2) = sqrt(23.2) = 4.8166378
        self.assertAlmostEqual(fm.initial_norm(N, "replay_tool"), 4.8166378, delta=1e-5)


class TestFirstLoggedUpdate(unittest.TestCase):
    """A shared FMUv6C log (4S) shows, at its first sample with current: I = 0.00549374986 A,
    V = 16.2002678 V, logged estimation_covariance_norm 0.960527122 and voltage_prediction 16.2001572.

    Hand arithmetic from a fresh reset, P = diag(a, b), x = [1, -I], s = a + b I^2:
      P'00 = a (lam + b I^2) / (lam (lam + s)), P'11 = b (lam + a) / (lam (lam + s)), P'01 = a b I / (lam (lam + s)).
    firmware (a = 6, b = 0.4, lam = 0.95): P'00 = 0.8633188, P'11 = 0.4210519, P'01 = 0.0019970 -> norm 0.9605270.
    tool     (a = 4.8, b = 0.4, lam = 0.99): P'00 = 0.8290239, P'11 = 0.4040396, P'01 = 0.0018402 -> norm 0.9222448.
    The firmware reset runs in the same call, so e0 = 16.2002678 and the prediction is
    16.2002678 - 0.00549374986 * 0.02 = 16.2001579.
    """

    V = 16.2002678
    I = 0.00549374986

    def test_firmware_reproduces_the_log(self):
        est = fm.Est(N, self.V, self.I, "firmware")
        r = est.step(self.V, self.I)
        self.assertTrue(r["accepted"])
        self.assertAlmostEqual(est.norm, 0.960527122, delta=1e-5)
        self.assertAlmostEqual(r["prediction"], 16.2001572, delta=3e-6)

    def test_tool_settings_do_not(self):
        est = fm.Est(N, self.V, self.I, "replay_tool")
        r = est.step(self.V, self.I)
        self.assertTrue(r["accepted"])
        self.assertAlmostEqual(est.norm, 0.9222448, delta=2e-5)
        self.assertGreater(abs(est.norm - 0.960527122), 0.03)


class TestHandling(unittest.TestCase):
    def test_low_current(self):
        fw = fm.Est(N, 16.0, 0.0, "firmware")
        self.assertIsNone(fw.step(16.0, 0.0))
        self.assertIsNone(fw.step(16.0, fm.FLT_EPSILON))
        self.assertIsNotNone(fw.step(16.0, 1.0))
        tool = fm.Est(N, 16.0, 0.0, "replay_tool")
        self.assertIsNotNone(tool.step(16.0, 0.0))

    def test_ties(self):
        self.assertTrue(fm.accepts(1.0, 1.0, True))
        self.assertFalse(fm.accepts(1.0, 1.0, False))
        self.assertTrue(fm.accepts(0.9, 1.0, False))
        self.assertTrue(fm.accepts(0.9, 1.0, True))
        self.assertFalse(fm.accepts(1.1, 1.0, True))
        self.assertFalse(fm.accepts(1.1, 1.0, False))

    def test_tool_ocv_seed(self):
        tool = fm.Est(N, 16.0, 10.0, "replay_tool")
        self.assertAlmostEqual(tool.e0, 16.0 + 0.005 * 4 * 10.0, delta=1e-12)
        fw = fm.Est(N, 16.0, 10.0, "firmware")
        self.assertEqual(fw.e0, 16.0)

    def test_frobenius_equals_px4_norm_for_symmetric(self):
        self.assertAlmostEqual(fm.matrix_norm(1.0, 0.3, 0.3, 2.0, "frobenius"),
                               fm.matrix_norm(1.0, 0.3, 0.3, 2.0, "px4"), delta=1e-15)


def synthetic_log():
    """A log written by the firmware model itself: 10 idle rows at the reset state, then 800 rows with current."""
    init = fm.initial_norm(N, "firmware")
    rows = [{"t_us": -1e6 + k * 1e4, "v": 16.4, "i": 0.0, "r": 0.005, "norm": init, "pred": 0.0, "landed": 1}
            for k in range(10)]
    est = fm.Est(N, 16.4, 0.0, "firmware")
    pred = 0.0
    for k in range(800):
        cur = 0.5 + 14.5 * k / 299.0 if k < 300 else 12.0
        volt = 16.4 - 0.034 * cur + 0.01 * math.sin(0.7 * k)
        if 20 <= k < 30:
            volt += 0.05 * (k - 20)   # co-moving disturbance during the ramp
        res = est.step(volt, cur)
        if res is not None:
            pred = res["prediction"]
        rows.append({"t_us": k * 1e4, "v": volt, "i": cur, "r": est.r_pub(), "norm": est.norm, "pred": pred,
                     "landed": 0 if k >= 100 else 1})
    return rows


class TestCompare(unittest.TestCase):
    def setUp(self):
        self.rows = synthetic_log()

    def test_seed(self):
        self.assertEqual(fm.find_seed(self.rows, N), 9)
        self.assertIsNone(fm.find_seed(self.rows[10:], N))

    def test_firmware_matches_its_own_log_exactly(self):
        c = fm.compare(self.rows, N, "firmware")
        self.assertEqual(c["rows_compared"], 800)
        self.assertEqual(c["decision_agree_share"], 1.0)
        self.assertIsNone(c["first_disagreement_t_s"])
        self.assertEqual(c["norm_max_rel_err"], 0.0)
        self.assertEqual(c["pred_abs_err_max_v"], 0.0)
        self.assertEqual(c["accepted_total_model"], c["accepted_total_logged"])
        self.assertGreater(c["accepted_total_logged"], 0)
        self.assertEqual(c["airborne_rows"], 700)
        self.assertEqual(c["airborne_zero_published_model"], c["airborne_zero_published_logged"])

    def test_other_settings_run_on_the_same_rows(self):
        for name in ("replay_tool", "tool_constants_fw_handling", "fw_constants_tool_handling"):
            for start in ("seed", "first"):
                c = fm.compare(self.rows, N, name, start)
                self.assertEqual(c["rows_compared"], 800)
                self.assertGreaterEqual(c["decision_agree_share"], 0.0)
                self.assertLessEqual(c["decision_agree_share"], 1.0)

    def test_tool_differs_on_the_log(self):
        c = fm.compare(self.rows, N, "replay_tool", "first")
        self.assertGreater(c["norm_max_rel_err"], 0.0)


class TestParse(unittest.TestCase):
    def test_parse(self):
        good = {"time_from_takeoff_us": "-104498095", "voltage_v": "16.2002678", "current_a": "0.00549374986",
                "internal_resistance_estimate": "0.00499999104", "estimation_covariance_norm": "0.960527122",
                "voltage_prediction": "16.2001572", "prediction_error": "0.000110626221",
                "armed_last_reported": "0", "landed_last_reported": "1"}
        bad = dict(good, voltage_v="")
        rows = fm.parse_battery_csv_rows([good, bad])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["landed"], 1)
        self.assertAlmostEqual(rows[0]["norm"], 0.960527122, delta=1e-12)


if __name__ == "__main__":
    unittest.main()

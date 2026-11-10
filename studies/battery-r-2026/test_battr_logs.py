"""Synthetic tests for battr_logs.py (no flight log, no network). They run before the census reads
any log. By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import unittest

import numpy as np

import battr_logs as bl


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


class Pieces(unittest.TestCase):
    def test_wanted(self):
        self.assertTrue(bl.wanted({"battery_status": "uint64_t timestamp;float internal_resistance_estimate;"}))
        self.assertFalse(bl.wanted({"battery_status": "uint64_t timestamp;float voltage_v;"}))
        self.assertFalse(bl.wanted({}))

    def test_last_change_frac(self):
        self.assertEqual(bl.last_change_frac([1, 1, 2, 2, 2]), 0.5)      # last change at 2 of 4
        self.assertEqual(bl.last_change_frac([3, 3, 3]), 0.0)
        self.assertEqual(bl.last_change_frac([1, 2]), 1.0)
        self.assertEqual(bl.last_change_frac([7]), 0.0)

    def test_estimator_active(self):
        self.assertTrue(bl.estimator_active(battery([0.005] * 10, [5.0] * 10)))
        self.assertFalse(bl.estimator_active(battery([0.0] * 10, [5.0] * 10, cov=np.zeros(10))))
        self.assertFalse(bl.estimator_active(battery([0.0] * 10, [5.0] * 10, cov=np.full(10, 0.3))))
        b = battery([0.0] * 10, [5.0] * 10)
        del b["estimation_covariance_norm"]
        self.assertFalse(bl.estimator_active(b))

    def test_step_fit(self):
        cur = steps(200)
        volt = 16.0 - 0.04 * cur                      # pack 40 mOhm, 4 cells: 10 mOhm per cell
        s = bl.step_fit(np.arange(200) * 200000, volt, cur, 4)
        self.assertEqual(s["pairs"], 199)
        self.assertEqual(s["range_a"], 10.0)
        self.assertAlmostEqual(s["mohm_per_cell"], 10.0, places=3)
        self.assertEqual(s["r"], -1.0)
        self.assertTrue(bl.step_valid(s))
        s = bl.step_fit(np.arange(100) * 200000, 16.0 - 0.04 * steps(100), steps(100), 4)
        self.assertFalse(bl.step_valid(s))            # 99 pairs < 150

    def test_step_fit_skips_gaps(self):
        t = np.array([0, 200000, 2000000, 2200000])   # the middle pair is 1.8 s apart
        s = bl.step_fit(t, [16.0, 15.6, 16.0, 15.6], [5.0, 15.0, 5.0, 15.0], 4)
        self.assertEqual(s["pairs"], 2)

    def test_cells(self):
        b = battery([0.005] * 5, [5.0] * 5, cells=6)
        self.assertEqual(bl.cells_of(b, np.ones(5, dtype=bool), {}), (6, "battery_status"))
        b = battery([0.005] * 5, [5.0] * 5, cells=0)
        self.assertEqual(bl.cells_of(b, np.ones(5, dtype=bool), {"BAT1_N_CELLS": 3}), (3, "BAT1_N_CELLS"))
        self.assertEqual(bl.cells_of(b, np.ones(5, dtype=bool), {}), (None, None))

    def test_eligible_basics(self):
        base = {"log_id": "x", "log_date": "2026-01-01", "duration_s": 300, "sys_hw": "PX4_FMU_V6C",
                "ver_sw_release": ""}
        self.assertEqual(bl.eligible(dict(base, sys_hw="PX4_SITL"))[1], "simulation")
        self.assertEqual(bl.eligible(dict(base, log_date="2024-12-31"))[1], "before 2025-01-01")
        self.assertEqual(bl.eligible(dict(base, duration_s=60))[1], "shorter than 120 s")
        self.assertEqual(bl.eligible(dict(base, source="CI"))[1], "CI")


class Measure(unittest.TestCase):
    def test_collapse(self):
        est = [0.005] * 100 + [0.0] * 200            # zero from sample 100 (20 s) to the end
        cur = steps(300)
        m = bl.measure_data(flight(battery(est, cur, volt=16.0 - 0.04 * cur)))
        self.assertTrue(m["measurable"])
        self.assertEqual(m["samples"], 300)
        self.assertEqual(m["airborne_s"], 60.0)
        self.assertTrue(m["collapsed"])
        self.assertTrue(m["zero_end"])
        self.assertEqual(m["zero_share"], 0.6667)     # 200 / 300
        self.assertEqual(m["first_zero_s"], 20.0)
        self.assertEqual(m["last_change_frac"], 0.3344)   # 100 / 299
        self.assertFalse(m["froze_early"])
        self.assertEqual((m["r_start_mohm"], m["r_end_mohm"], m["changes"]), (5.0, 0.0, 1))
        self.assertEqual(m["cells"], 4)
        self.assertTrue(m["step_valid"])
        self.assertAlmostEqual(m["step"]["mohm_per_cell"], 10.0, places=3)

    def test_froze_early(self):
        est = [0.005] * 10 + [0.0042] * 290
        m = bl.measure_data(flight(battery(est, steps(300))))
        self.assertEqual(m["last_change_frac"], 0.0334)   # 10 / 299
        self.assertTrue(m["froze_early"])
        self.assertFalse(m["collapsed"])
        self.assertFalse(m["ever_zero"])
        self.assertIsNone(m["first_zero_s"])
        self.assertEqual(m["r_end_mohm"], 4.2)
        self.assertFalse(m["step_valid"])                # no voltage field

    def test_zero_only_in_the_first_half_is_not_a_collapse(self):
        est = [0.0] * 100 + [0.006] * 200
        m = bl.measure_data(flight(battery(est, steps(300))))
        self.assertTrue(m["ever_zero"])
        self.assertFalse(m["collapsed"])
        self.assertFalse(m["zero_end"])
        self.assertEqual(m["first_zero_s"], 0.0)

    def test_not_measurable(self):
        m = bl.measure_data(flight(battery([0.005] * 300, [0.2] * 300)))
        self.assertEqual(m["why_not"], "median airborne current under 0.5 A")
        m = bl.measure_data(flight(battery([0.005] * 300, steps(300)), landed=1))
        self.assertEqual(m["why_not"], "fewer than 150 airborne samples")
        m = bl.measure_data(flight(battery([0.0] * 300, steps(300), cov=np.zeros(300))))
        self.assertEqual(m["why_not"], "no active estimator with valid current")
        self.assertEqual(m["active_instances"], [])
        m = bl.measure_data({})
        self.assertEqual(m["read"], "no battery_status data")

    def test_picks_the_active_instance(self):
        dead = battery([0.0] * 300, [30.0] * 300, cov=np.zeros(300))
        live = battery([0.005] * 300, steps(300))
        data = flight(live)
        data["battery_status"][1] = dead
        m = bl.measure_data(data)
        self.assertEqual(m["battery_instance"], 0)
        self.assertEqual(m["active_instances"], [0])


def rec(i, collapsed, froze, r_end, step, rint=-1.0):
    return {"order": i, "params": {"BAT1_R_INTERNAL": rint}, "status": "estimate field",
            "measure": {"read": "ok", "measurable": True, "collapsed": collapsed, "froze_early": froze,
                        "r_end_mohm": r_end, "step_valid": step is not None, "step": {"mohm_per_cell": step},
                        "zero_end": collapsed, "ever_zero": collapsed, "changes": 1,
                        "last_change_frac": 0.1 if froze else 0.5,
                        "first_zero_s": 10.0 if collapsed else None, "airborne_from": "land_detected"}}


class Summarise(unittest.TestCase):
    def test_grades(self):
        recs = [rec(1, True, True, 0.0, 9.0), rec(2, True, True, 0.0, None)]
        for k in range(20):                           # alive: r_end 1..20 mOhm, step reversed
            recs.append(rec(3 + k, False, k < 10, float(k + 1), float(20 - k)))
        recs.append({"order": 99, "status": "no estimate field", "params": {}})
        s = bl.summarise(recs)
        p, rep = s["predictions"], s["reported"]
        self.assertTrue(p["R1"])                      # 2 / 22 = 9.1% >= 5%
        self.assertTrue(p["R2"])                      # 12 / 22 = 54.5% >= 50%
        self.assertTrue(p["R3"])                      # median of 1..20 = 10.5
        self.assertTrue(p["R4"])                      # rho = -1 on 20 pairs
        self.assertTrue(p["R5"])                      # 22 / 22 at -1
        self.assertEqual(rep["measurable"], 22)
        self.assertEqual(rep["collapsed"], 2)
        self.assertEqual(rep["r4_pairs"], 20)
        self.assertEqual(rep["spearman_r_end_vs_step"], -1.0)
        self.assertEqual(rep["r_end_alive_median_mohm"], 10.5)
        self.assertEqual(rep["froze_early"], 12)

    def test_too_few_is_not_graded(self):
        recs = [rec(k, False, False, 5.0, None, rint=0.004) for k in range(19)]
        p = bl.summarise(recs)["predictions"]
        self.assertEqual(p, {"R1": None, "R2": None, "R3": None, "R4": None, "R5": None})

    def test_fails(self):
        recs = [rec(k, False, False, 30.0, None, rint=0.004) for k in range(25)]
        p = bl.summarise(recs)["predictions"]
        self.assertFalse(p["R1"])                     # 0 collapsed
        self.assertFalse(p["R2"])
        self.assertFalse(p["R3"])                     # median 30 mOhm > 20
        self.assertIsNone(p["R4"])                    # no step fits
        self.assertFalse(p["R5"])                     # none at -1

    def test_spearman(self):
        self.assertAlmostEqual(bl.spearman([1, 2, 3], [1, 2, 3]), 1.0)
        self.assertAlmostEqual(bl.spearman([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertEqual(bl.ranks([5, 1, 5]), [2.5, 1.0, 2.5])


if __name__ == "__main__":
    unittest.main()

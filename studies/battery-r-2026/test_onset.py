"""Synthetic tests for onset.py (no flight log, no network). They run before the pass reads any
log. By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import json
import os
import tempfile
import unittest

import numpy as np

import onset as on

US = 200000                                            # 5 Hz


def battery(est, cur):
    n = len(est)
    return {"timestamp": np.arange(n, dtype=np.int64) * US,
            "internal_resistance_estimate": np.asarray(est, dtype=np.float64),
            "current_a": np.asarray(cur, dtype=np.float64),
            "estimation_covariance_norm": np.linspace(1.0, 0.1, n),
            "cell_count": np.full(n, 4)}


def flight(b, takeoff_i, armed_i):
    return {"battery_status": {0: b},
            "vehicle_land_detected": {0: {"timestamp": np.array([0, takeoff_i * US], dtype=np.int64),
                                          "landed": np.array([1, 0])}},
            "vehicle_status": {0: {"timestamp": np.array([0, armed_i * US], dtype=np.int64),
                                   "arming_state": np.array([1, 2])}}}


def steps(n):
    return np.array([5.0 if i % 2 == 0 else 15.0 for i in range(n)])


CUR = np.concatenate([np.full(50, 0.7), steps(350)])   # 0.7 A on the ground, 5/15 A in the air


class Phases(unittest.TestCase):
    def test_phases(self):
        t = np.arange(10, dtype=np.int64) * US
        ph = on.phases(t, land=(np.array([0, 5 * US]), np.array([1, 0])),
                       status=(np.array([0, 2 * US]), np.array([1, 2])))
        self.assertEqual(list(ph), [0, 0, 1, 1, 1, 2, 2, 2, 2, 2])

    def test_unknown_without_status(self):
        t = np.arange(4, dtype=np.int64) * US
        ph = on.phases(t, land=(np.array([0, 2 * US]), np.array([1, 0])))
        self.assertEqual(list(ph), [-1, -1, 2, 2])


class Onset(unittest.TestCase):
    def test_before_the_log(self):
        m = on.measure_data(flight(battery([0.0] * 400, CUR), 50, 0))
        self.assertTrue(m["measurable"])
        self.assertTrue(m["collapsed"])
        self.assertEqual(m["log_start_phase"], "armed on ground")
        self.assertEqual(m["onset_phase"], "before the log")
        self.assertIsNone(m["onset_current_a"])
        self.assertIsNone(m["onset_s_from_takeoff"])
        self.assertTrue(on.before_takeoff(m))

    def test_on_the_ground(self):
        m = on.measure_data(flight(battery([0.005] * 30 + [0.0] * 370, CUR), 50, 20))
        self.assertTrue(m["collapsed"])
        self.assertEqual(m["log_start_phase"], "disarmed")
        self.assertEqual(m["onset_phase"], "armed on ground")
        self.assertEqual(m["onset_current_a"], 0.7)
        self.assertEqual(m["onset_s_from_takeoff"], -4.0)      # sample 30 vs takeoff at sample 50
        self.assertTrue(on.before_takeoff(m))

    def test_in_flight(self):
        m = on.measure_data(flight(battery([0.005] * 120 + [0.0] * 280, CUR), 50, 20))
        self.assertTrue(m["collapsed"])
        self.assertEqual(m["onset_phase"], "airborne")
        self.assertEqual(m["onset_current_a"], 5.0)             # sample 120 = step 70, even
        self.assertEqual(m["onset_s_from_takeoff"], 14.0)
        self.assertFalse(on.before_takeoff(m))

    def test_not_collapsed_has_no_onset(self):
        m = on.measure_data(flight(battery([0.005] * 400, CUR), 50, 20))
        self.assertTrue(m["measurable"])
        self.assertFalse(m["collapsed"])
        self.assertNotIn("onset_phase", m)
        self.assertEqual(m["log_start_phase"], "disarmed")

    def test_not_measurable_passes_through(self):
        m = on.measure_data({})
        self.assertEqual(m["read"], "no battery_status data")
        self.assertNotIn("log_start_phase", m)


def rec(i, collapsed, phase=None, onset_s=None, cur=None, r_end=3.0, step=None):
    m = {"read": "ok", "measurable": True, "collapsed": collapsed, "r_end_mohm": 0.0 if collapsed else r_end,
         "step_valid": step is not None, "step": {"mohm_per_cell": step}, "log_start_phase": "armed on ground"}
    if collapsed:
        m.update({"onset_phase": phase, "onset_current_a": cur, "onset_s_from_takeoff": onset_s,
                  "first_zero_s": 0.0})
    return {"order": i, "params": {"BAT1_R_INTERNAL": -1.0}, "status": "estimate field", "measure": m,
            "sys_hw": "X", "mav_type": "Quadrotor", "ver_sw_release": "v1.16.0 255"}


class Summarise(unittest.TestCase):
    def test_grades(self):
        recs = [rec(1, True, "before the log"), rec(2, True, "armed on ground", -4.0, 0.7),
                rec(3, True, "disarmed", -30.0, 1.0), rec(4, True, "airborne", 14.0, 5.0),
                rec(5, True, "airborne", 3.0, 12.0)]
        for k in range(20):
            recs.append(rec(6 + k, False, r_end=float(k + 1), step=float(k + 1)))
        recs.append({"order": 99, "status": "no estimate field", "params": {}})
        s = on.summarise(recs)
        p, rep = s["predictions"], s["reported"]
        self.assertTrue(p["O1"])                      # 5 / 25 = 20% >= 5%
        self.assertTrue(p["O2"])                      # 3 / 5 before takeoff
        self.assertFalse(p["O3"])                     # median of 0.7, 1.0, 5.0, 12.0 = 3.0 A
        self.assertTrue(p["O4"])                      # rho = 1 on 20 pairs
        self.assertEqual(rep["measurable"], 25)
        self.assertEqual(rep["before_takeoff"], 3)
        self.assertEqual(rep["onset_current_median_a"], 3.0)
        self.assertEqual(rep["onset_s_from_takeoff"], [-30.0, -4.0, 3.0, 14.0])
        self.assertEqual(rep["onset_phases"], {"airborne": 2, "armed on ground": 1, "before the log": 1,
                                               "disarmed": 1})
        self.assertEqual(rep["o4_pairs"], 20)
        self.assertEqual(rep["spearman_r_end_vs_step"], 1.0)
        json.dumps(s)

    def test_too_few_is_not_graded(self):
        recs = [rec(k, False) for k in range(19)]
        p = on.summarise(recs)["predictions"]
        self.assertEqual(p, {"O1": None, "O2": None, "O3": None, "O4": None})

    def test_first_pass_vehicles(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "rows.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"vehicle": "a"}) + "\n\n" + json.dumps({"vehicle": "b"}) + "\n")
        self.assertEqual(on.first_pass_vehicles(path), {"a", "b"})


if __name__ == "__main__":
    unittest.main()

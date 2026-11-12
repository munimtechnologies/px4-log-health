"""Synthetic tests for cost.py (px4-log-health battery-r-2026, cost). No log is read.
Needs battr_logs.py 0.1.0 next to it for the measure_cost tests.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import math
import os
import tempfile
import unittest
import urllib.error

import numpy as np

import cost

R_CELL = 0.004
SPAN = 4.05 - 3.6
PARAMS = {"BAT1_V_EMPTY": 3.6, "BAT1_V_CHARGED": 4.05, "BAT1_CAPACITY": 5000.0,
          "BAT1_R_INTERNAL": -1.0, "BAT1_N_CELLS": 4}
NAN = float("nan")


def flight(collapsed=True, n=600):
    k = np.arange(n, dtype=np.float64)
    t = (1000000 + 200000 * np.arange(n)).astype(np.int64)
    cur = 10.0 + 5.0 * np.sin(2 * math.pi * k / 50.0) + 6.0 * ((np.arange(n) % 37) < 10)
    cell = 4.0 - 0.00006 * k - R_CELL * cur
    if collapsed:
        est = np.zeros(n)
        rem = 0.9 - 0.0005 * k - (R_CELL / SPAN) * cur
    else:
        est = np.full(n, R_CELL)
        rem = 0.9 - 0.0005 * k
    dis = np.cumsum(cur * 0.2 / 3600.0 * 1000.0)
    bat = {"timestamp": t, "voltage_v": 4 * cell, "current_a": cur, "remaining": rem,
           "internal_resistance_estimate": est,
           "estimation_covariance_norm": np.linspace(1.0, 0.06, n),
           "discharged_mah": dis, "cell_count": np.full(n, 4), "connected": np.ones(n)}
    land = {"timestamp": np.array([0], dtype=np.int64), "landed": np.array([0])}
    return {"battery_status": {0: bat}, "vehicle_land_detected": {0: land}}


class ParamTests(unittest.TestCase):
    def test_param_lookup_and_span(self):
        self.assertEqual(cost.param({"BAT2_CAPACITY": 3000, "BAT1_CAPACITY": 5000}, "CAPACITY", 1), 3000.0)
        self.assertEqual(cost.param({"BAT1_CAPACITY": 5000}, "CAPACITY", 1), 5000.0)
        self.assertEqual(cost.param({"BAT_CAPACITY": 4000}, "CAPACITY", 0), 4000.0)
        self.assertIsNone(cost.param({}, "CAPACITY", 0))
        self.assertAlmostEqual(cost.span_of(PARAMS, 0), 0.45, places=9)
        self.assertIsNone(cost.span_of({"BAT1_V_EMPTY": 4.1, "BAT1_V_CHARGED": 4.05}, 0))

    def test_r_used(self):
        self.assertEqual(cost.r_used_of({"BAT1_R_INTERNAL": 0.003}, 0, 0.0), 0.003)
        self.assertEqual(cost.r_used_of({"BAT1_R_INTERNAL": -1}, 0, 0.002), 0.002)
        self.assertIsNone(cost.r_used_of({}, 0, None))

    def test_k_expected(self):
        self.assertAlmostEqual(cost.k_expected(3.0, 0.0, 0.45), -0.003 / 0.45, places=12)
        self.assertAlmostEqual(cost.k_expected(3.0, 0.002, 0.5), -0.002, places=12)
        self.assertIsNone(cost.k_expected(None, 0.0, 0.45))
        self.assertIsNone(cost.k_expected(3.0, 0.0, None))


class NonFiniteTests(unittest.TestCase):
    """0.1.1: a NaN or infinite value is never used as a number."""

    def test_finite(self):
        self.assertTrue(cost.finite(0.0))
        self.assertTrue(cost.finite(-3))
        self.assertFalse(cost.finite(None))
        self.assertFalse(cost.finite(NAN))
        self.assertFalse(cost.finite(float("inf")))
        self.assertFalse(cost.finite("x"))

    def test_nan_estimate_is_not_known(self):
        self.assertIsNone(cost.r_used_of({"BAT1_R_INTERNAL": -1}, 0, NAN))
        self.assertEqual(cost.r_used_of({"BAT1_R_INTERNAL": 0.003}, 0, NAN), 0.003)
        self.assertIsNone(cost.k_expected(3.0, NAN, 0.45))
        self.assertIsNone(cost.k_expected(NAN, 0.0, 0.45))

    def test_median_finite(self):
        v = np.array([NAN, 1.0, 2.0, 3.0, NAN])
        m = np.array([True, True, True, False, True])
        self.assertEqual(cost.median_finite(v, m), 1.5)
        self.assertIsNone(cost.median_finite(np.array([NAN, NAN]), np.array([True, True])))
        self.assertIsNone(cost.median_finite(np.array([1.0, 2.0]), np.array([False, False])))


class MaskAndFitTests(unittest.TestCase):
    def test_active_mask(self):
        good = np.array([True, True, True, False])
        rem = np.array([0.5, 0.885, 0.95, 0.1])
        dis = np.array([100.0, 100.0, 100.0, 100.0])
        self.assertEqual(list(cost.active_mask(good, rem, dis, 1000.0)), [True, True, False, False])
        self.assertEqual(list(cost.active_mask(good, rem, dis, 0.0)), [True, True, True, False])
        self.assertEqual(list(cost.active_mask(good, rem, None, 1000.0)), [True, True, True, False])

    def test_fit_k_exact(self):
        n = 300
        t = 5000000 + 200000 * np.arange(n)
        i = 8.0 + 4.0 * np.cos(np.arange(n) / 7.0)
        y = 0.7 - 1e-4 * np.arange(n) + 2e-7 * np.arange(n) ** 2 - 0.006 * i
        k, sd = cost.fit_k(t, y, i)
        self.assertAlmostEqual(k, -0.006, places=9)
        self.assertLess(sd, 1e-9)


class MeasureTests(unittest.TestCase):
    def test_collapsed_flight_shows_the_sag(self):
        m = cost.measure_cost(flight(True), PARAMS)
        self.assertTrue(m["census"]["collapsed"])
        self.assertTrue(m["census"]["step_valid"])
        self.assertAlmostEqual(m["census"]["step"]["mohm_per_cell"], 4.0, places=1)
        self.assertTrue(m["measured"], m.get("why_not"))
        self.assertEqual(m["active"], 600)
        self.assertEqual(m["estimate_nonfinite_active"], 0)
        self.assertEqual(m["r_used_mohm"], 0.0)
        self.assertAlmostEqual(m["k"], -R_CELL / SPAN, places=6)
        self.assertAlmostEqual(m["k_exp"], -R_CELL / SPAN, places=4)

    def test_working_estimate_cancels(self):
        m = cost.measure_cost(flight(False), PARAMS)
        self.assertFalse(m["census"]["collapsed"])
        self.assertTrue(m["measured"], m.get("why_not"))
        self.assertAlmostEqual(m["r_used_mohm"], 4.0, places=6)
        self.assertLess(abs(m["k"]), 1e-8)
        self.assertLess(abs(m["k_exp"]), 1e-6)

    def test_set_resistance_wins(self):
        p = dict(PARAMS, BAT1_R_INTERNAL=0.002)
        m = cost.measure_cost(flight(True), p)
        self.assertAlmostEqual(m["r_used_mohm"], 2.0, places=6)
        self.assertAlmostEqual(m["k_exp"], -0.002 / SPAN, places=4)

    def test_coulomb_bound_removes_samples(self):
        p = dict(PARAMS, BAT1_CAPACITY=420.0)
        m = cost.measure_cost(flight(True), p)
        self.assertLess(m["active"], 600)
        self.assertGreater(m["active"], 0)

    def test_short_flight_not_measured(self):
        m = cost.measure_cost(flight(True, n=160), dict(PARAMS, BAT1_CAPACITY=0.0))
        # 160 airborne samples pass the census, but the bound is off and all are active
        self.assertTrue(m["census"]["measurable"])
        self.assertEqual(m["active"], 160)
        self.assertTrue(m["measured"], m.get("why_not"))
        m2 = cost.measure_cost(flight(True, n=160), dict(PARAMS, BAT1_CAPACITY=60.0))
        self.assertFalse(m2["measured"])
        self.assertIn("active samples", m2["why_not"])

    def test_no_remaining_field(self):
        d = flight(True)
        del d["battery_status"][0]["remaining"]
        m = cost.measure_cost(d, PARAMS)
        self.assertFalse(m["measured"])
        self.assertEqual(m["why_not"], "no remaining field")


def meas(collapsed, k, k_exp, measured=True):
    return {"measured": measured, "census": {"collapsed": collapsed}, "k": k, "k_exp": k_exp,
            "active_current_median_a": 10.0, "active_share": 0.9}


class GradeTests(unittest.TestCase):
    def sets(self, ratio_c=0.8, n_c=6, n_a=20):
        c = [meas(True, ratio_c * (-0.010 - 0.001 * j), -0.010 - 0.001 * j) for j in range(n_c)]
        a = [meas(False, 0.5 * (-0.002 + 0.0001 * j), -0.002 + 0.0001 * j) for j in range(n_a)]
        return c + a

    def test_all_hold(self):
        p, rep = cost.grade(self.sets())
        self.assertEqual(p, {"P1": True, "P2": True, "P3": True})
        self.assertEqual(rep["C"], 6)
        self.assertEqual(rep["A"], 20)
        self.assertAlmostEqual(rep["p1_median_k_over_k_exp"], 0.8, places=4)
        self.assertAlmostEqual(rep["p3_rho"], 1.0, places=4)
        # collapsed k = 0.8 x k_exp, k_exp from -0.010 to -0.015 per A; at 10 A the median
        # under-read is 0.8 x 0.0125 x 10 x 100 = 10 percentage points
        self.assertAlmostEqual(rep["under_read_pp_at_median_current_C"]["median"], 10.0, places=4)

    def test_p1_fails(self):
        p, _ = cost.grade(self.sets(ratio_c=0.2))
        self.assertFalse(p["P1"])

    def test_too_few_not_graded(self):
        p, _ = cost.grade(self.sets(n_c=3, n_a=20))
        self.assertEqual(p, {"P1": None, "P2": None, "P3": None})

    def test_unmeasured_and_positive_k_exp_excluded(self):
        ms = self.sets() + [meas(True, -0.05, 0.001), meas(True, -0.05, -0.01, measured=False)]
        p, rep = cost.grade(ms)
        self.assertEqual(rep["C"], 6)
        self.assertEqual(rep["collapsed_measured"], 7)

    def test_nonfinite_values_kept_out(self):
        # a NaN k_exp is "not known" (kept out of C and A); a NaN k is not a measurement
        ms = self.sets() + [meas(False, -0.001, NAN), meas(True, -0.02, NAN),
                            meas(False, NAN, -0.001)]
        p, rep = cost.grade(ms)
        self.assertEqual(p, {"P1": True, "P2": True, "P3": True})
        self.assertEqual(rep["C"], 6)
        self.assertEqual(rep["A"], 20)
        self.assertEqual(rep["measured"], 28)
        self.assertEqual(rep["k_exp_not_known"], {"collapsed": 1, "alive": 1})
        self.assertAlmostEqual(rep["p3_rho"], 1.0, places=4)


class AnalyticTests(unittest.TestCase):
    def test_analytic(self):
        def row(lid, collapsed, r_end, cur, valid=True):
            return {"log_id": lid, "measure": {"replayed": True, "census": {
                "step_valid": valid, "step": {"mohm_per_cell": 3.0}, "collapsed": collapsed,
                "r_end_mohm": r_end, "current_median_a": cur, "battery_instance": 0}}}
        base = {"BAT1_V_EMPTY": 3.6, "BAT1_V_CHARGED": 4.05, "BAT1_R_INTERNAL": -1}
        rows = [row("a", True, 0.0, 15.0), row("b", False, 2.0, 10.0), row("c", False, 2.0, 10.0),
                row("d", True, 0.0, 15.0, valid=False), row("e", True, 0.0, 15.0),
                {"log_id": "f", "measure": {"replayed": False}}]
        params = cost.params_by_log([[{"log_id": "a", "params": base}, {"log_id": "b", "params": base},
                                      {"log_id": "c", "params": dict(base, BAT1_R_INTERNAL=0.003)},
                                      {"log_id": "d", "params": base}]])
        res = cost.analytic(rows, params)
        self.assertEqual(res["flights"], 3)
        self.assertEqual(res["collapsed"], 1)
        self.assertEqual(res["skipped"], {"no valid step fit": 1, "no parameters in the rows": 1})
        self.assertEqual(res["collapsed_rows"][0]["uncompensated_pp"], 10.0)
        self.assertEqual(res["collapsed_rows"][0]["full_sag_pp"], 10.0)
        self.assertEqual(res["uncompensated_pp_alive"]["n"], 2)
        self.assertEqual(sorted([res["uncompensated_pp_alive"]["p10"], res["uncompensated_pp_alive"]["max"]]),
                         [0.0, 2.222])

    def test_analytic_nan_end_estimate_is_missing(self):
        rows = [{"log_id": "a", "measure": {"replayed": True, "census": {
            "step_valid": True, "step": {"mohm_per_cell": 3.0}, "collapsed": False,
            "r_end_mohm": NAN, "current_median_a": 10.0, "battery_instance": 0}}}]
        params = {"a": {"BAT1_V_EMPTY": 3.6, "BAT1_V_CHARGED": 4.05, "BAT1_R_INTERNAL": -1}}
        res = cost.analytic(rows, params)
        self.assertEqual(res["flights"], 0)
        self.assertEqual(res["skipped"], {"missing value": 1})


class PassTests(unittest.TestCase):
    def test_targets_order(self):
        rows = [{"log_id": "x", "order": 1, "measure": {"replayed": True, "census": {"collapsed": False}}},
                {"log_id": "y", "order": 2, "measure": {"replayed": True, "census": {"collapsed": True}}},
                {"log_id": "z", "order": 3, "measure": {"replayed": False}},
                {"log_id": "y", "order": 4, "measure": {"replayed": True, "census": {"collapsed": True}}}]
        ts = cost.load_targets(rows)
        self.assertEqual([t["log_id"] for t in ts], ["y", "x"])

    def test_run_stops_on_block_and_deletes(self):
        tmp = os.path.join(tempfile.mkdtemp(), "x.ulg")
        calls = []

        def fetch(lid, path):
            calls.append(lid)
            if lid == "b":
                raise urllib.error.HTTPError("u", 403, "blocked", None, None)
            with open(path, "wb") as f:
                f.write(b"x")
            return {"has_estimate": True}, path

        out = []
        res = cost.run([{"log_id": "a"}, {"log_id": "b"}, {"log_id": "c"}], fetch,
                       lambda p: {"read": "ok"}, out.append, tmp, sleep=lambda s: None)
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(res["stop"], "blocked: http 403")
        self.assertEqual(res["downloaded"], 1)
        self.assertFalse(os.path.exists(tmp))
        self.assertEqual(out[0]["measure"], {"read": "ok"})


if __name__ == "__main__":
    unittest.main()

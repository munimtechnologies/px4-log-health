"""Synthetic tests for replay.py (px4-log-health battery-r-2026 replay). No real log is read.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import json
import os
import random
import tempfile
import unittest
import urllib.error

import numpy as np

import replay as rp

T0 = 10000000          # us
DT = 200000            # 5 Hz, as battery_status is logged


def currents(n, lo=3.0, hi=30.0, seed=7):
    rng = random.Random(seed)
    return np.array([lo + (hi - lo) * rng.random() for _ in range(n)])


def series(cur, volt_fn):
    n = len(cur)
    t = T0 + np.arange(n, dtype=np.int64) * DT
    volt = np.array([volt_fn(c, k) for k, c in enumerate(cur)], dtype=np.float64)
    return t, volt, np.asarray(cur, dtype=np.float64), np.ones(n, dtype=bool)


class TestPort(unittest.TestCase):
    def test_initial_state_is_px4s(self):
        r = rp.Rls(4)
        self.assertAlmostEqual(float(r.e0), 16.8, places=4)
        self.assertAlmostEqual(float(r.e1), 0.02, places=6)
        self.assertAlmostEqual(float(r.norm), (36.0 + 0.16) ** 0.5, places=4)
        self.assertAlmostEqual(float(r.r_pub), 0.005, places=7)
        self.assertEqual(r.p00.dtype, np.float32)

    def test_one_update_by_hand(self):
        # reset at 16 V; then V = 15.6 V at 10 A. Worked by hand in PLAN-replay.md's appendix.
        r = rp.Rls(4)
        r.reset(16.0, 1.0)
        self.assertTrue(r.update(15.6, 10.0))
        self.assertAlmostEqual(float(r.e0), 15.974441, places=4)
        self.assertAlmostEqual(float(r.e1), 0.0370394, places=5)
        self.assertAlmostEqual(float(r.p00), 5.508660, places=3)
        self.assertAlmostEqual(float(r.p01), 0.538086, places=4)
        self.assertAlmostEqual(float(r.p10), 0.538086, places=4)
        self.assertAlmostEqual(float(r.p11), 0.0623284, places=5)
        self.assertAlmostEqual(float(r.r_pub), 0.00925985, places=5)
        self.assertLess(float(r.norm), 6.0133)

    def test_rejected_update_only_moves_ocv(self):
        f = np.float32
        r = rp.Rls(4)
        r.e0, r.e1 = f(16.0), f(0.02)
        r.p00, r.p01, r.p10, r.p11 = f(0.04), f(0.0), f(0.0), f(1e-6)
        r.norm = r._norm(r.p00, r.p10, r.p11)
        r.r_pub = f(0.005)
        before = (float(r.p00), float(r.p11), float(r.norm))
        self.assertFalse(r.update(15.5, 1.0))
        self.assertAlmostEqual(float(r.e0), 15.52, places=4)
        self.assertAlmostEqual(float(r.e1), 0.02, places=7)
        self.assertEqual((float(r.p00), float(r.p11), float(r.norm)), before)
        self.assertAlmostEqual(float(r.r_pub), 0.005, places=7)
        # the same step without the gate is accepted
        u = rp.Rls(4, gated=False)
        u.e0, u.e1 = f(16.0), f(0.02)
        u.p00, u.p01, u.p10, u.p11 = f(0.04), f(0.0), f(0.0), f(1e-6)
        u.norm = u._norm(u.p00, u.p10, u.p11)
        self.assertTrue(u.update(15.5, 1.0))


class TestReplay(unittest.TestCase):
    def test_clean_data_converges(self):
        t, v, i, c = series(currents(600), lambda cur, k: 16.0 - 0.04 * cur)
        out = rp.replay(t, v, i, c, 4)
        self.assertAlmostEqual(out["r_pub"][-1] * 1000.0, 10.0, delta=2.0)
        plain = rp.replay(t, v, i, c, 4, gated=False, dtype=np.float64)
        self.assertAlmostEqual(plain["r_raw"][-1] * 1000.0, 10.0, delta=0.5)

    def test_voltage_rising_with_current_publishes_zero(self):
        t, v, i, c = series(currents(600), lambda cur, k: 16.0 + 0.02 * cur)
        out = rp.replay(t, v, i, c, 4)
        self.assertEqual(out["r_pub"][-1], 0.0)
        self.assertLess(out["r_raw"][-1], 0.0)
        self.assertTrue(np.all(out["r_pub"][~np.isnan(out["r_pub"])] >= 0.0))

    def test_gate_depends_on_current_only(self):
        cur = currents(500)
        a = rp.replay(*series(cur, lambda x, k: 16.0 - 0.04 * x), n_cells=4)
        b = rp.replay(*series(cur, lambda x, k: 15.0 + 0.03 * x + 0.1 * np.sin(k)), n_cells=4)
        self.assertTrue(np.array_equal(a["accepted"], b["accepted"]))
        self.assertTrue(np.array_equal(a["norm"], b["norm"]))

    def test_norm_never_reaches_floor(self):
        for cur in (currents(1500), currents(1500, 0.0, 1.0, seed=3), np.full(1500, 0.3)):
            t, v, i, c = series(cur, lambda x, k: 16.0 - 0.04 * x)
            g = rp.replay(t, v, i, c, 4)
            u = rp.replay(t, v, i, c, 4, gated=False, dtype=np.float64)
            self.assertGreaterEqual(np.nanmin(g["norm"]), rp.FLOOR_TOL)
            self.assertGreaterEqual(np.nanmin(u["norm"]), rp.FLOOR_TOL)

    def test_norm_is_a_ratchet_after_the_reset_window(self):
        t, v, i, c = series(currents(800), lambda x, k: 16.0 - 0.04 * x)
        g = rp.replay(t, v, i, c, 4)
        after = t > T0 + rp.INIT_US
        self.assertTrue(np.all(np.diff(g["norm"][after]) <= 0.0))

    def test_reset_window_resets_every_sample(self):
        t, v, i, c = series(currents(50), lambda x, k: 16.0 - 0.04 * x)
        g = rp.replay(t, v, i, c, 4)
        # the first 11 samples (0.0 .. 2.0 s) reset then update once: same starting norm each time
        inside = t <= T0 + rp.INIT_US
        self.assertEqual(int(np.sum(inside)), 11)
        self.assertTrue(np.all(g["norm"][inside] < 6.0134))

    def test_constant_current_freezes(self):
        t, v, i, c = series(np.full(400, 10.0), lambda x, k: 16.0 - 0.04 * x - 0.0005 * k)
        g = rp.replay(t, v, i, c, 4)
        self.assertEqual(int(np.sum(g["accepted"][200:])), 0)
        self.assertEqual(len(set(g["r_pub"][200:].tolist())), 1)

    def test_disconnection_resets_again(self):
        cur = currents(300)
        t, v, i, c = series(cur, lambda x, k: 16.0 - 0.04 * x)
        v[150] = 0.0                      # under 2.1 V: PX4 treats the pack as disconnected
        g = rp.replay(t, v, i, c, 4)
        self.assertGreater(g["norm"][152], g["norm"][149])
        self.assertTrue(np.all(np.diff(g["norm"][170:]) <= 0.0))

    def test_no_update_without_current(self):
        cur = currents(100)
        cur[60:] = -1.0                   # invalid current
        t, v, i, c = series(cur, lambda x, k: 16.0 - 0.04 * max(x, 0.0))
        g = rp.replay(t, v, i, c, 4)
        self.assertFalse(np.any(g["updated"][60:]))
        self.assertEqual(len(set(g["r_pub"][60:].tolist())), 1)


class TestNormCheck(unittest.TestCase):
    def test_increases_and_resets(self):
        t = np.arange(8, dtype=np.int64) * 2000000
        cov = np.array([0.5, 0.4, 0.4, 0.3, 0.9, 0.8, 0.85, 0.7])
        volt = np.full(8, 16.0)
        conn = np.ones(8, dtype=bool)
        conn[4] = False
        out = rp.norm_check(t, cov, volt, conn, None, 4, np.ones(8, dtype=bool))
        self.assertEqual(out["increases"], 2)
        self.assertEqual(out["unexplained"], 1)
        self.assertAlmostEqual(out["max_unexplained_rel"], 0.0625, places=6)
        self.assertEqual(out["min_norm"], 0.3)
        self.assertEqual(out["air_pairs"], 7)
        self.assertEqual(out["air_decreases"], 4)

    def test_reset_value_and_cell_change_explain(self):
        t = np.arange(5, dtype=np.int64) * 5000000
        reset = 4 * (1.5 ** 2 + 0.1 ** 2) ** 0.5
        cov = np.array([0.0, 0.3, reset, 0.2, 0.25])
        volt = np.full(5, 16.0)
        conn = np.ones(5, dtype=bool)
        cells = np.array([4, 4, 4, 4, 6])
        out = rp.norm_check(t, cov, volt, conn, cells, 4)
        self.assertEqual(out["increases"], 3)
        self.assertEqual(out["unexplained"], 0)
        self.assertEqual(out["nonpositive"], 1)
        self.assertIsNone(out["air_accept_share"])


def synthetic_log(volt_fn, est_value=0.008):
    n = 1500
    t = T0 + np.arange(n, dtype=np.int64) * DT
    rng = random.Random(11)
    flying = (t >= T0 + 30000000) & (t < T0 + 280000000)
    cur = np.array([5.0 + 25.0 * rng.random() if f else 0.8 for f in flying])
    volt = np.array([volt_fn(c) for c in cur])
    bat = {"timestamp": t, "current_a": cur, "voltage_v": volt,
           "internal_resistance_estimate": np.full(n, est_value),
           "estimation_covariance_norm": np.linspace(0.5, 0.06, n),
           "cell_count": np.full(n, 4, dtype=np.uint8), "connected": np.ones(n, dtype=np.uint8)}
    lt = np.array([T0, T0 + 30000000, T0 + 280000000], dtype=np.int64)
    land = {"timestamp": lt, "landed": np.array([1, 0, 1], dtype=np.uint8)}
    return {"battery_status": {0: bat}, "vehicle_land_detected": {0: land}}


class TestMeasure(unittest.TestCase):
    def test_clean_flight(self):
        m = rp.measure_replay(synthetic_log(lambda c: 16.8 - 0.04 * c), {"BAT1_R_INTERNAL": -1.0})
        self.assertTrue(m["replayed"])
        self.assertFalse(m["census"]["collapsed"])
        self.assertTrue(m["census"]["step_valid"])
        self.assertFalse(m["replay"]["collapsed"])
        self.assertAlmostEqual(m["replay"]["r_end_mohm"], 10.0, delta=2.0)
        self.assertTrue(m["plain"]["positive"])
        self.assertAlmostEqual(m["plain"]["median_second_half_mohm"], 10.0, delta=1.0)
        self.assertEqual(m["norm"]["unexplained"], 0)
        self.assertGreater(m["replay"]["updates_air"], 1000)
        json.dumps(m, default=lambda o: o.item() if hasattr(o, "item") else str(o))

    def test_flat_flight_collapses_in_replay(self):
        m = rp.measure_replay(synthetic_log(lambda c: 16.8 + 0.01 * c, est_value=0.0))
        self.assertTrue(m["census"]["collapsed"])
        self.assertTrue(m["replay"]["collapsed"])
        self.assertFalse(m["plain"]["positive"])

    def test_not_measurable_is_not_replayed(self):
        d = synthetic_log(lambda c: 16.8 - 0.04 * c)
        d["vehicle_land_detected"][0]["landed"] = np.array([1, 1, 1], dtype=np.uint8)
        m = rp.measure_replay(d)
        self.assertFalse(m["replayed"])


def fake(collapsed, r, replay_collapsed, r_end=5.0, replay_end=5.0, valid=True, plain_pos=True,
         unexplained=0, min_norm=0.06):
    return {"replayed": True,
            "norm": {"unexplained": unexplained, "min_norm": min_norm, "air_accept_share": 0.1,
                     "end_norm": 0.07, "max_unexplained_rel": 0.0},
            "census": {"collapsed": collapsed, "step": {"r": r}, "step_valid": valid,
                       "r_end_mohm": r_end, "first_zero_s": 0.0 if collapsed else None},
            "replay": {"collapsed": replay_collapsed, "r_end_mohm": replay_end,
                       "raw_median_second_half_mohm": 1.0, "accepted_air": 5, "updates_air": 50},
            "plain": {"positive": plain_pos, "median_second_half_mohm": 2.0}}


class TestGrade(unittest.TestCase):
    def test_rules(self):
        ms = [fake(False, -0.6, False, r_end=k + 1.0, replay_end=k + 1.0) for k in range(40)]
        ms += [fake(True, 0.2, k < 4, r_end=0.0, replay_end=0.0, valid=False, plain_pos=False)
               for k in range(6)]
        ms += [fake(True, -0.5, False, r_end=0.0) for _ in range(4)]
        ms.append({"replayed": False})
        p, rep = rp.grade(ms)
        self.assertEqual(p, {"C1": True, "C2": True, "P1": True, "P2": True, "P3": None, "P4": True,
                             "P5": None})
        self.assertEqual(rep["replayed"], 50)
        self.assertEqual(rep["p2_reproduced"], 4)
        self.assertEqual(rep["p4_pairs"], 40)
        self.assertAlmostEqual(rep["p4_rho"], 1.0)
        self.assertEqual(rep["steep_collapsed"], 4)

    def test_failures_and_minimums(self):
        ms = [fake(False, -0.6, k < 10, unexplained=1 if k < 3 else 0) for k in range(44)]
        ms += [fake(True, -0.5, True, r_end=0.0, plain_pos=False) for _ in range(6)]
        p, rep = rp.grade(ms)
        self.assertFalse(p["C1"])          # 47 / 50 = 0.94
        self.assertFalse(p["P1"])          # 34 / 44
        self.assertIsNone(p["P2"])         # no flat collapse
        self.assertFalse(p["P3"])          # 0 / 6
        self.assertFalse(p["P5"])          # 0 / 6


class TestTargetsAndRun(unittest.TestCase):
    def _write(self, d, name, rows):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return p

    def test_order_and_dedupe(self):
        def row(lid, order, measurable=True, collapsed=False):
            return {"log_id": lid, "order": order,
                    "measure": {"read": "ok", "measurable": measurable, "collapsed": collapsed,
                                "step": {"r": -0.5}, "r_end_mohm": 1.0}}
        with tempfile.TemporaryDirectory() as d:
            a = self._write(d, "a.jsonl", [row("a1", 1), row("a2", 2, collapsed=True), row("a3", 3, False)])
            b = self._write(d, "b.jsonl", [row("b1", 1, collapsed=True), row("a1", 2)])
            c = self._write(d, "c.jsonl", [row("c1", 1)])
            got = rp.load_targets([("first", a), ("onset", b), ("steps", c)])
        self.assertEqual([x["log_id"] for x in got], ["a2", "b1", "a1", "c1"])
        self.assertEqual([x["pass"] for x in got], ["first", "onset", "first", "steps"])

    def test_run_stops_when_blocked(self):
        calls = []

        def fetch(lid, path):
            calls.append(lid)
            if len(calls) == 2:
                raise urllib.error.HTTPError("u", 429, "slow down", {}, None)
            return {"has_estimate": False}, None
        recs = []
        targets = [{"log_id": "x%d" % k, "row_collapsed": False} for k in range(5)]
        res = rp.run(targets, fetch, None, recs.append, "tmp", sleep=lambda s: None)
        self.assertEqual(res["stop"], "blocked: http 429")
        self.assertEqual(res["requested"], 2)
        self.assertEqual([r["status"] for r in recs], ["no estimate field", "http 429"])


if __name__ == "__main__":
    unittest.main()

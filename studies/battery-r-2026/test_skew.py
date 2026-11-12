"""Synthetic tests for skew.py 0.1.0 (battery-r-2026, timing offset). No flight log; no network;
no simulation result is printed. By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import math
import unittest

import numpy as np

import replay as rp
import sim
import skew


def sequences(n=700, s=3, seed=11):
    """Idle at 0.8 A with noise, a step to 3 A at sample 300, a swinging current from sample 500."""
    rng = np.random.default_rng(seed)
    cur = np.empty((n, s))
    cur[:300] = 0.8
    cur[300:500] = 3.0
    tf = np.arange(n - 500) / 100.0
    cur[500:] = 15.0 + 4.0 * np.sin(2 * math.pi * 0.2 * tf)[:, None]
    volt = 16.4 - 0.0108 * cur + rng.normal(0.0, 0.01, size=cur.shape)
    cur = cur + rng.normal(0.0, 0.05, size=cur.shape)
    t = np.arange(n, dtype=np.int64) * 10000
    return t, sim.quant(volt, 0.0145), sim.quant(cur, 0.029)


class Estimator(unittest.TestCase):
    def test_run_est_equals_sim(self):
        t, volt, cur = sequences()
        a = skew.run_est(t, volt, cur, 4)
        b = sim.run_estimator(t, volt, cur, 4)
        for key in ("r_pub", "r_raw", "norm", "accepted"):
            self.assertTrue(np.array_equal(a[key], b[key]), key)

    def test_run_est_equals_replay(self):
        t, volt, cur = sequences(s=1)
        a = skew.run_est(t, volt[:, 0], cur[:, 0], 4)
        ref = rp.replay(t, volt[:, 0], cur[:, 0], np.ones(len(t), dtype=bool), 4)
        self.assertTrue(np.array_equal(a["r_pub"][:, 0].astype(np.float64), ref["r_pub"]))
        self.assertTrue(np.array_equal(a["accepted"][:, 0], ref["accepted"]))

    def test_keep_leaves_ocv_on_reject(self):
        f = np.float32
        px4 = sim.VecRls(4, 1)
        keep = skew.KeepRls(4, 1)
        v0 = np.array([16.4], dtype=f)
        px4.reset(v0)
        keep.reset(v0)
        act = np.array([True])
        i = np.array([0.8], dtype=f)
        seen_reject = False
        for k in range(3000):
            v = np.array([16.4 + 0.0145 * (k % 2)], dtype=f)
            e0_keep = keep.e0.copy()
            acc_p = px4.update(v, i, act)
            acc_k = keep.update(v, i, act)
            if not seen_reject:
                # identical until the first rejection
                self.assertEqual(bool(acc_p[0]), bool(acc_k[0]))
                self.assertEqual(px4.e0[0], keep.e0[0])
                self.assertEqual(px4.e1[0], keep.e1[0])
            if not acc_k[0]:
                self.assertEqual(keep.e0[0], e0_keep[0])
            if not acc_p[0] and not seen_reject:
                seen_reject = True
                self.assertEqual(px4.e0[0], f(v[0] + px4.e1[0] * i[0]))
        self.assertTrue(seen_reject)

    def test_keep_runs_through_run_est(self):
        t, volt, cur = sequences(s=2)
        a = skew.run_est(t, volt, cur, 4, skew.KeepRls)
        self.assertEqual(a["r_pub"].shape, (len(t), 2))
        self.assertTrue(np.all(np.isfinite(a["r_pub"])))

    def test_rejects_disconnected(self):
        t, volt, cur = sequences(s=1)
        volt = volt.copy()
        volt[10, 0] = 1.0
        with self.assertRaises(ValueError):
            skew.run_est(t, volt, cur, 4)


class Model(unittest.TestCase):
    def test_ramp_zero_is_identity(self):
        cur = sim.true_current(np.random.default_rng(1), 3, 0)
        self.assertTrue(np.array_equal(skew.ramp(cur, 0), cur))

    def test_ramp_by_hand(self):
        cur = sim.true_current(np.random.default_rng(2), 2, 0)
        r = skew.ramp(cur, 25)
        n_g, n_s, _ = sim.steps()
        self.assertAlmostEqual(float(r[n_g, 0]), 0.8 + (1.0 / 26.0) * 2.2, places=12)
        self.assertAlmostEqual(float(r[n_g + 24, 0]), 0.8 + (25.0 / 26.0) * 2.2, places=12)
        self.assertEqual(float(r[n_g + 25, 0]), 3.0)
        k0 = n_g + n_s
        for j in (0, 12, 24):
            w = (j + 1) / 26.0
            self.assertAlmostEqual(float(r[k0 + j, 1]), 3.0 + w * (cur[k0 + j, 1] - 3.0), places=12)
        self.assertTrue(np.array_equal(r[k0 + 25:], cur[k0 + 25:]))
        self.assertTrue(np.array_equal(r[:n_g], cur[:n_g]))

    def test_measured_directions(self):
        q = sim.Q_V
        volt = (1000.0 + np.arange(8, dtype=float))[:, None] * q
        cur = (100.0 + np.arange(8, dtype=float))[:, None] * sim.Q_I
        z = np.zeros_like(volt)
        v, i = skew.measured(volt, cur, z, z, 1)
        self.assertTrue(np.allclose(v, volt))
        self.assertTrue(np.allclose(i[1:], cur[:-1]))
        v, i = skew.measured(volt, cur, z, z, -2)
        self.assertTrue(np.allclose(i, cur))
        self.assertTrue(np.allclose(v[2:], volt[:-2]))
        self.assertTrue(np.allclose(v[:2], volt[0]))
        v, i = skew.measured(volt, cur, z, z, 0)
        self.assertTrue(np.allclose(v, volt) and np.allclose(i, cur))

    def test_cell_data_shapes_and_none(self):
        t, cur, volt, ev, ei = skew.cell_data(np.random.default_rng(3), 4, "none", 25)
        self.assertEqual(cur.shape, (18300, 4))
        self.assertTrue(np.all(ev == 0.0) and np.all(ei == 0.0))
        t, cur, volt, ev, ei = skew.cell_data(np.random.default_rng(3), 4, "typical", 0)
        self.assertTrue(0.01 < float(np.std(ev)) < 0.02)
        self.assertEqual(int(t[1] - t[0]), 10000)


class Outcomes(unittest.TestCase):
    def test_cell_stats(self):
        n_g = 4
        r = np.full((12, 3), 0.0027, dtype=np.float32)
        r[5:, 1] = 0.0
        r[11, 2] = 0.0013
        est = {"r_pub": r, "accepted": np.zeros(r.shape, dtype=bool)}
        old = skew.AFTER_SPOOL
        skew.AFTER_SPOOL = 2
        try:
            st = skew.cell_stats(est, 6, n_g)
        finally:
            skew.AFTER_SPOOL = old
        self.assertEqual(st["n"], 3)
        self.assertEqual(st["low_end"], 2)
        self.assertEqual(st["in_flight"], 1)
        self.assertEqual(st["at_takeoff"], 1)
        self.assertAlmostEqual(st["r_end_median"], float(np.float32(0.0013)), places=9)

    def test_run_pair_smoke(self):
        res = skew.run_pair("quiet", 0, seeds=2)
        self.assertEqual(len(res), len(skew.SKEWS) * len(skew.VARIANTS))
        for st in res.values():
            self.assertEqual(st["n"], 2)


def mk(low, n=100, med=0.0027):
    return {"n": n, "low_end": low, "in_flight": 0, "at_takeoff": 0, "r_end_median": med}


def cells_from(fn):
    out = {}
    for v in skew.VARIANTS:
        for nz in skew.NOISE_ORDER:
            for ri in skew.RISES:
                for sk in skew.SKEWS:
                    out[(v, nz, ri, sk)] = fn(v, nz, ri, sk)
    return out


class Grade(unittest.TestCase):
    def test_all_hold(self):
        def fn(v, nz, ri, sk):
            if sk == 0:
                return mk(5, med=0.0027)
            if ri == 25:
                return mk(20)
            if v == "keep" and sk > 0:
                return mk(10)
            return mk(80, med=0.0002)
        pred, rep = skew.grade(cells_from(fn))
        self.assertEqual(pred, {"K1": True, "K2": True, "K3": True, "K4": True, "K5": True, "K6": True,
                                "K7": True})
        self.assertEqual(rep["k1_aligned_low"], [20, 400, 0.05])
        self.assertEqual(rep["k4_current_late_low"]["keep"], [80, 800, 0.1])
        self.assertEqual(rep["k7_offset_1_low"]["jump"], [640, 800, 0.8])

    def test_fail_and_not_graded(self):
        def fn(v, nz, ri, sk):
            if sk == 0:
                return mk(50, med=0.0027)
            return mk(10, med=0.002)
        pred, _ = skew.grade(cells_from(fn))
        self.assertEqual(pred["K1"], False)
        self.assertEqual(pred["K2"], False)
        self.assertEqual(pred["K3"], False)
        self.assertIsNone(pred["K4"])
        self.assertIsNone(pred["K5"])
        self.assertIsNone(pred["K7"])
        self.assertEqual(pred["K6"], False)

    def test_boundaries(self):
        # K1 at exactly 10%, K2 at exactly 50%, K4 at exactly half, K5 at exactly half, K7 at exactly half
        def fn(v, nz, ri, sk):
            if sk == 0:
                return mk(10, med=0.002)
            if ri == 25:
                return mk(25)
            if v == "keep" and sk > 0:
                return mk(25)
            if v == "keep" and sk < 0:
                return mk(25)
            return mk(50, med=0.0013)
        pred, _ = skew.grade(cells_from(fn))
        self.assertEqual(pred, {"K1": True, "K2": True, "K3": True, "K4": True, "K5": True, "K6": True,
                                "K7": True})

    def test_k6_aligned_out_of_range(self):
        def fn(v, nz, ri, sk):
            return mk(80, med=0.0002)
        pred, _ = skew.grade(cells_from(fn))
        self.assertEqual(pred["K6"], False)


if __name__ == "__main__":
    unittest.main()

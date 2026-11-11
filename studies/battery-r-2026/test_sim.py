"""Synthetic tests for sim.py 0.1.0 (battery-r-2026, simulation). The first group requires the
vectorised estimator to equal replay.replay() exactly. No flight log; no network.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import math
import os
import tempfile
import unittest

import numpy as np

import replay as rp
import sim


def sequences(n=900, s=4, seed=7):
    """Ground at 0.8 A (some exact zeros), steps 0.8/2.3 A, then a swinging flight current. Seed 2
    has voltage rising with current (a negative resistance in the data)."""
    rng = np.random.default_rng(seed)
    cur = np.empty((n, s))
    cur[:400] = 0.8
    cur[150:160] = 0.0
    k = np.arange(400, 600)
    cur[400:600] = np.where(((k - 400) // 25) % 2 == 0, 0.8, 2.3)[:, None]
    tf = np.arange(n - 600) / 100.0
    cur[600:] = 15.0 + 4.0 * np.sin(2 * math.pi * 0.2 * tf)[:, None]
    cur = cur + rng.normal(0.0, 0.05, size=cur.shape)
    cur[150:160] = 0.0
    slope = np.array([-0.011, -0.004, 0.02, -0.03])[:s]
    volt = 16.4 + slope[None, :] * cur + rng.normal(0.0, 0.01, size=cur.shape)
    volt = sim.quant(volt, 0.0145)
    cur = sim.quant(cur, 0.029)
    t = np.arange(n, dtype=np.int64) * 10000
    return t, volt, cur


class MatchesReplay(unittest.TestCase):
    def test_equal_to_replay_replay(self):
        t, volt, cur = sequences()
        out = sim.run_estimator(t, volt, cur, 4)
        acc_total = rej_total = 0
        for j in range(volt.shape[1]):
            ref = rp.replay(t, volt[:, j], cur[:, j], np.ones(len(t), dtype=bool), 4)
            for key in ("r_pub", "r_raw", "norm"):
                self.assertTrue(np.array_equal(out[key][:, j].astype(np.float64), ref[key]),
                                "%s differs on seed %d" % (key, j))
            self.assertTrue(np.array_equal(out["accepted"][:, j], ref["accepted"]))
            acc_total += int(np.sum(ref["accepted"]))
            rej_total += int(np.sum(ref["updated"] & ~ref["accepted"]))
        self.assertGreater(acc_total, 0)
        self.assertGreater(rej_total, 0)

    def test_single_run_shape(self):
        t, volt, cur = sequences(s=1)
        out = sim.run_estimator(t, volt[:, 0], cur[:, 0], 4)
        ref = rp.replay(t, volt[:, 0], cur[:, 0], np.ones(len(t), dtype=bool), 4)
        self.assertEqual(out["r_pub"].shape, (len(t), 1))
        self.assertTrue(np.array_equal(out["r_pub"][:, 0].astype(np.float64), ref["r_pub"]))

    def test_inactive_samples_change_nothing(self):
        t, volt, cur = sequences(s=1)
        out = sim.run_estimator(t, volt, cur, 4)
        # samples 150-159 have current exactly 0: no update (PX4 updates only above FLT_EPSILON)
        self.assertFalse(np.any(out["accepted"][150:160]))

    def test_prior_option(self):
        t, volt, cur = sequences(s=2)
        a = sim.run_estimator(t, volt, cur, 4)
        b = sim.run_estimator(t, volt, cur, 4, r_cov=rp.R_COVARIANCE)
        for key in ("r_pub", "r_raw", "norm"):
            self.assertTrue(np.array_equal(a[key], b[key]))
        est = sim.VecRls(4, 2, r_cov=sim.SMALL_R_COV)
        self.assertEqual(est.p11[0], np.float32(0.001) * np.float32(4))
        self.assertEqual(est.p00[0], np.float32(1.5) * np.float32(4))

    def test_start_is_a_fresh_connection(self):
        t, volt, cur = sequences(s=3)
        a = sim.run_estimator(t, volt, cur, 4, start=300)
        b = sim.run_estimator(t[300:], volt[300:], cur[300:], 4)
        for key in ("r_pub", "r_raw", "norm", "accepted"):
            self.assertTrue(np.array_equal(a[key][300:], b[key]))
        self.assertTrue(np.all(a["r_pub"][:300] == np.float32(rp.R_DEFAULT)))
        self.assertFalse(np.any(a["accepted"][:300]))

    def test_rejects_disconnected_samples(self):
        t, volt, cur = sequences(s=1)
        volt = volt.copy()
        volt[500, 0] = 1.0
        with self.assertRaises(ValueError):
            sim.run_estimator(t, volt, cur, 4)


class Model(unittest.TestCase):
    def test_quant(self):
        self.assertAlmostEqual(float(sim.quant(0.0144, 0.0145)), 0.0145, places=12)
        self.assertEqual(float(sim.quant(0.007, 0.0145)), 0.0)
        self.assertAlmostEqual(float(sim.quant(16.39, 0.0145)), round(16.39 / 0.0145) * 0.0145, places=12)

    def test_true_voltage_by_hand(self):
        v = sim.true_voltage(np.full(3, 0.8))
        self.assertAlmostEqual(float(v[0, 0]), 4 * (4.10 - 0.0027 * 0.8), places=12)
        ah = 0.8 * 0.01 / 3600.0
        vrc = 0.0005 * 0.001 * 0.8
        self.assertAlmostEqual(float(v[1, 0]), 4 * (4.10 - 0.60 * ah / 5.0 - 0.0027 * 0.8 - vrc), places=12)

    def test_rc_closed_form(self):
        n = 1001
        v = sim.true_voltage(np.full(n, 10.0))
        k = 1000
        ah = 10.0 * k * 0.01 / 3600.0
        vrc = 0.001 * 10.0 * (1.0 - (1.0 - 0.0005) ** k)
        self.assertAlmostEqual(float(v[k, 0]), 4 * (4.10 - 0.60 * ah / 5.0 - 0.0027 * 10.0 - vrc), places=9)

    def test_shift(self):
        x = np.arange(6, dtype=float)[:, None]
        self.assertEqual(list(sim.shift(x, 2)[:, 0]), [0.0, 0.0, 0.0, 1.0, 2.0, 3.0])
        self.assertEqual(list(sim.shift(x, 0)[:, 0]), list(x[:, 0]))

    def test_steps(self):
        self.assertEqual(sim.steps(), (6000, 300, 12000))

    def test_true_current(self):
        rng = np.random.default_rng(1)
        cur = sim.true_current(rng, 20, 0)
        self.assertEqual(cur.shape, (18300, 20))
        self.assertTrue(np.all(cur[:6000] == 0.8))
        self.assertTrue(np.all(cur[6000:6300] == 3.0))
        self.assertTrue(abs(float(np.mean(cur[6300:])) - 15.0) < 1.0)
        cur = sim.true_current(np.random.default_rng(2), 20, 1)
        for j in range(20):
            high = int(np.sum(cur[:6000, j] > 1.8))
            self.assertTrue(30 <= high <= 150, high)
            self.assertTrue(np.all(cur[:500, j] == 0.8))

    def test_inputs(self):
        t, cur, volt, vm, im = sim.inputs(np.random.default_rng(3), 5, "typical", 0, 2)
        self.assertEqual(t[1] - t[0], 10000)
        self.assertEqual(vm.shape, (18300, 5))
        steps_v = vm / 0.0145
        self.assertTrue(np.allclose(steps_v, np.round(steps_v), atol=1e-6))
        self.assertTrue(np.all(np.abs(vm - volt) < 0.2))
        self.assertTrue(np.all(np.abs(im[2:] - cur[:-2]) < 1.0))


class Outcomes(unittest.TestCase):
    def test_plan_appendix(self):
        r = np.array([[5, 5, 5], [5, 5, 5], [5, 5, 5], [0, 2, 0], [0, 0, 1],
                      [0, 0, 1], [0, 0, 1], [0, 3, 1], [0, 0, 1], [0, 0, 1]], dtype=np.float32)
        o = sim.outcomes(r, 4)
        self.assertEqual(list(o["at_takeoff"]), [True, False, True])
        self.assertEqual(list(o["in_flight"]), [True, False, False])
        self.assertEqual(list(o["r_end"]), [0.0, 0.0, 1.0])

    def test_cell_stats(self):
        r = np.array([[5, 5], [0, 5], [0, 5], [0, 5], [0, 5], [0, 5]], dtype=np.float32) / 1000.0
        est = {"r_pub": r, "norm": np.ones_like(r), "accepted": np.zeros(r.shape, dtype=bool)}
        st = sim.cell_stats(est, 3)
        self.assertEqual((st["n"], st["at_takeoff"], st["in_flight"], st["both"]), (2, 1, 1, 1))
        self.assertEqual(st["touched_zero_on_ground"], 1)
        self.assertEqual(st["first_zero_s_quantiles"]["median"], 0.01)
        self.assertEqual(len(st["rel_err"]), 1)
        self.assertAlmostEqual(st["rel_err"][0], abs(0.005 / 0.0027 - 1.0), places=5)

    def test_run_cell_smoke(self):
        res = sim.run_cell("quiet", 0, 0, seeds=2)
        self.assertEqual(sorted(res), ["f1", "f2", "px4"])
        for v in res:
            self.assertEqual(res[v]["n"], 2)


def mk(at, inf, both, n=100, rel=0.2):
    return {"n": n, "at_takeoff": at, "in_flight": inf, "both": both, "rel_err": [rel] * (n - inf)}


def cells_from(px4, f1, f2):
    out = {}
    for noise in sim.NOISE_ORDER:
        for ev in sim.EVENT_SETTINGS:
            for sk in sim.SKEWS:
                out[("px4", noise, ev, sk)] = px4(noise)
                out[("f1", noise, ev, sk)] = f1(noise)
                out[("f2", noise, ev, sk)] = f2(noise)
    return out


class Grade(unittest.TestCase):
    def test_mixed(self):
        at = {"quiet": 5, "typical": 10, "noisy": 25}
        both = {"quiet": 4, "typical": 8, "noisy": 20}
        cells = cells_from(lambda nz: mk(at[nz], both[nz], both[nz]),
                           lambda nz: mk(6, 0, 0), lambda nz: mk(0, 6, 0))
        pred, rep = sim.grade(cells)
        self.assertEqual(pred, {"H1": True, "H2": True, "H3": True, "H4": True, "H5": False, "H6": True})
        self.assertEqual(rep["h3_both"], 128)
        self.assertEqual(rep["h3_at_takeoff"], 160)
        self.assertEqual(rep["h2_diff"], 0.2)
        self.assertEqual(rep["f1_at_takeoff"], [72, 1200, 0.06])

    def test_too_few(self):
        def px4(nz):
            return mk(6, 0, 0) if nz == "quiet" else mk(0, 0, 0)
        cells = cells_from(px4, lambda nz: mk(0, 0, 0), lambda nz: mk(0, 0, 0))
        cells[("px4", "quiet", 0, 0)] = mk(6, 0, 0)
        pred, _ = sim.grade(cells)
        self.assertEqual(pred["H1"], False)
        self.assertEqual(pred["H2"], False)
        self.assertIsNone(pred["H3"])
        self.assertIsNone(pred["H4"])
        self.assertIsNone(pred["H5"])
        self.assertEqual(pred["H6"], True)

    def test_h6_fails_and_needs_runs(self):
        cells = cells_from(lambda nz: mk(0, 0, 0, rel=0.8), lambda nz: mk(0, 0, 0), lambda nz: mk(0, 0, 0))
        self.assertEqual(sim.grade(cells)[0]["H6"], False)
        cells = cells_from(lambda nz: mk(0, 95, 0, n=103), lambda nz: mk(0, 0, 0), lambda nz: mk(0, 0, 0))
        self.assertIsNone(sim.grade(cells)[0]["H6"])


class PlanGate(unittest.TestCase):
    def test_wait(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "PLAN.md")
        with open(p, "wb") as f:
            f.write(b"a\r\nb\n")
        self.assertTrue(sim.wait_for_plan(p, lambda: b"a\nb\n", 10, sleep=lambda s: None))
        clock = [0.0]

        def sleep(s):
            clock[0] += s
        self.assertFalse(sim.wait_for_plan(p, lambda: b"x", 900, sleep=sleep, clock=lambda: clock[0]))
        self.assertEqual(clock[0], 900)


if __name__ == "__main__":
    unittest.main()

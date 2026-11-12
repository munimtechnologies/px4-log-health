"""Synthetic tests for battr_trace.py 0.1.1 (battery-r-2026, trace, with NOTE-trace-align.md's timing
readouts). No flight log; no network.
'firmware()' below plays PX4 logging battery_status at full rate from the first sample: replay.py's
call pattern (reset during the first 2 s, update when current > FLT_EPSILON) plus the logged
voltage_prediction, which keeps its last value when there is no update.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import json
import math
import unittest

import numpy as np

import replay as rp
import battr_trace as tc


def synth(n=1500, seed=3, slope=-0.011):
    """100 Hz from t = 1 s: 6 s idle at 0.8 A, 3 s of 0.8 / 2.3 A steps, then a swinging 15 A flight
    with sample-to-sample load noise. Pack voltage 16.4 V + slope * I + noise, quantised to 14.5 mV
    and 29 mA, then rounded to float32 as a log stores it."""
    rng = np.random.default_rng(seed)
    cur = np.empty(n)
    cur[:600] = 0.8 + rng.normal(0.0, 0.05, 600)
    k = np.arange(600, 900)
    cur[600:900] = np.where(((k - 600) // 25) % 2 == 0, 0.8, 2.3) + rng.normal(0.0, 0.05, 300)
    tf = np.arange(n - 900) / 100.0
    cur[900:] = 15.0 + 4.0 * np.sin(2 * math.pi * 0.2 * tf) + rng.normal(0.0, 0.4, n - 900)
    volt = 16.4 + slope * cur + rng.normal(0.0, 0.01, n)
    volt = (np.round(volt / 0.0145) * 0.0145).astype(np.float32).astype(np.float64)
    cur = (np.round(cur / 0.029) * 0.029).astype(np.float32).astype(np.float64)
    t = 1000000 + np.arange(n, dtype=np.int64) * 10000
    return t, volt, cur


def firmware(t, volt, cur, n_cells=4):
    rls = rp.Rls(n_cells)
    m = len(t)
    out = {"r": np.zeros(m), "norm": np.zeros(m), "pred": np.zeros(m), "raw": np.zeros(m)}
    last = 0.0
    for k in range(m):
        v, i = float(volt[k]), float(cur[k])
        if int(t[k]) <= int(t[0]) + rp.INIT_US:
            rls.reset(v, i)
        if i > rp.FLT_EPSILON:
            last = float(tc.predict(rls, v, i))
            rls.update(v, i)
        out["pred"][k] = last
        out["r"][k] = float(rls.r_pub)
        out["norm"][k] = float(rls.norm)
        out["raw"][k] = float(rls.e1 / rls.n)
    return out


def bat_of(t, volt, cur, fw):
    return {"timestamp": t, "voltage_v": volt, "current_a": cur, "internal_resistance_estimate": fw["r"],
            "estimation_covariance_norm": fw["norm"], "voltage_prediction": fw["pred"]}


def one_step(cur_at, volt_at, n=100, down=False):
    """10 ms samples; the current steps 1 -> 6 A into sample cur_at and the voltage 16.0 -> 15.9 V
    into sample volt_at (None: the voltage never moves). down=True mirrors both steps."""
    k = np.arange(n)
    t = k.astype(np.int64) * 10000
    cur = np.where(k >= cur_at, 6.0, 1.0)
    volt = np.where(k >= volt_at, 15.9, 16.0) if volt_at is not None else np.full(n, 16.0)
    if down:
        cur = 7.0 - cur
        volt = 31.9 - volt
    return t, volt, cur


class Firmware(unittest.TestCase):
    def test_helper_equals_replay(self):
        t, volt, cur = synth()
        fw = firmware(t, volt, cur)
        ref = rp.replay(t, volt, cur, np.ones(len(t), dtype=bool), 4)
        self.assertTrue(np.array_equal(fw["r"], ref["r_pub"]))
        self.assertTrue(np.array_equal(fw["norm"], ref["norm"]))
        self.assertTrue(np.array_equal(fw["raw"], ref["r_raw"]))


class Anchor(unittest.TestCase):
    def test_full_log(self):
        # resets at t <= t0 + 2 s: samples 0..200
        t, volt, cur = synth()
        fw = firmware(t, volt, cur)
        a, cands = tc.find_anchor(t, volt, cur, fw["norm"], fw["pred"], 4)
        self.assertEqual(a, 200)
        self.assertEqual(cands[:201], list(range(201)))

    def test_log_starts_late(self):
        t, volt, cur = synth()
        fw = firmware(t, volt, cur)
        s = slice(50, None)
        a, _ = tc.find_anchor(t[s], volt[s], cur[s], fw["norm"][s], fw["pred"][s], 4)
        self.assertEqual(a, 150)
        tr = tc.trace(t[s], volt[s], cur[s], 4, a)
        for key, ref in (("r_pub", "r"), ("norm", "norm"), ("r_raw", "raw"), ("pred", "pred")):
            got = tr[key][a:]
            want = fw[ref][s][a:]
            self.assertTrue(np.array_equal(got, want), key)

    def test_no_reset_window_in_log(self):
        # the log starts 3 s after connection: no sample is a reset sample
        t, volt, cur = synth()
        fw = firmware(t, volt, cur)
        s = slice(300, None)
        a, _ = tc.find_anchor(t[s], volt[s], cur[s], fw["norm"][s], fw["pred"][s], 4)
        self.assertIsNone(a)


class Replay(unittest.TestCase):
    def test_trace_equals_firmware_and_agrees(self):
        t, volt, cur = synth()
        fw = firmware(t, volt, cur)
        tr = tc.trace(t, volt, cur, 4, 200)
        self.assertIsNone(tr["stop"])
        self.assertTrue(np.array_equal(tr["r_pub"][200:], fw["r"][200:]))
        self.assertTrue(np.array_equal(tr["norm"][200:], fw["norm"][200:]))
        self.assertTrue(np.all(np.isnan(tr["r_pub"][:200])))
        ag = tc.agreement(tr, fw["r"], fw["norm"], fw["pred"], 200, len(t) - 1)
        self.assertEqual(ag["samples"], len(t) - 200)
        self.assertEqual(ag["match"], ag["samples"])
        self.assertIsNone(ag["first_mismatch"])
        # d_raw is the change of raw R at each update
        k = int(np.flatnonzero(tr["accepted"][201:])[0]) + 201
        self.assertAlmostEqual(tr["d_raw"][k], tr["r_raw"][k] - tr["r_raw"][k - 1], places=12)

    def test_agreement_finds_a_changed_sample(self):
        t, volt, cur = synth()
        fw = firmware(t, volt, cur)
        tr = tc.trace(t, volt, cur, 4, 200)
        bad = fw["norm"].copy()
        bad[700] *= 1.01
        ag = tc.agreement(tr, fw["r"], bad, fw["pred"], 200, len(t) - 1)
        self.assertEqual(ag["first_mismatch"], 700)
        self.assertEqual(ag["match"], ag["samples"] - 1)

    def test_stops_at_disconnection(self):
        t, volt, cur = synth()
        v2 = volt.copy()
        v2[1000] = 0.0
        tr = tc.trace(t, v2, cur, 4, 200)
        self.assertEqual(tr["stop"], 1000)
        self.assertTrue(np.all(np.isnan(tr["r_pub"][1000:])))


class Recover(unittest.TestCase):
    def test_full_rate_recovers_raw_r(self):
        t, volt, cur = synth()
        fw = firmware(t, volt, cur)
        rec = tc.recover_raw(t, volt, cur, fw["r"], fw["norm"], fw["pred"], 4, start=201)
        self.assertGreaterEqual(len(rec["rows"]), 20)
        for k, r in rec["rows"]:
            self.assertLessEqual(abs(r - fw["raw"][k]), 2e-5, k)
        self.assertEqual(rec["agree"], rec["positive"])

    def test_negative_r_is_recovered_where_the_log_shows_0(self):
        t, volt, cur = synth(slope=0.02)
        fw = firmware(t, volt, cur)
        rec = tc.recover_raw(t, volt, cur, fw["r"], fw["norm"], fw["pred"], 4, start=201)
        neg = [(k, r) for k, r in rec["rows"] if fw["raw"][k] < -1e-3]
        self.assertGreater(len(neg), 0)
        for k, r in neg:
            self.assertEqual(fw["r"][k], 0.0)
            self.assertLessEqual(abs(r - fw["raw"][k]), 2e-5)

    def test_decimated_log_fails_the_check(self):
        t, volt, cur = synth()
        fw = firmware(t, volt, cur)
        d = slice(0, None, 5)
        rec = tc.recover_raw(t[d], volt[d], cur[d], fw["r"][d], fw["norm"][d], fw["pred"][d], 4, start=41)
        rows = rec["rows"]
        self.assertGreaterEqual(len(rows), 5)
        raw = fw["raw"][d]
        same = sum(1 for k, r in rows if abs(r - raw[k]) <= tc.RECOVER_TOL)
        self.assertLess(same, 0.5 * len(rows))

    def test_gate_g1(self):
        self.assertTrue(tc.g1({"positive": 20, "agree": 18}))
        self.assertFalse(tc.g1({"positive": 20, "agree": 17}))
        self.assertFalse(tc.g1({"positive": 19, "agree": 19}))

    def test_hand_example(self):
        # rejected update at k = 1 (norm unchanged), next update at k = 2 with a different current:
        # pred[2] = V[1] + R * (I[1] - I[2]); V[1] = 16.0, I 2.0 -> 1.0, R = -0.02 (pack) gives
        # pred[2] = 15.98, raw per cell = (15.98 - 16.0) / (2.0 - 1.0) / 4 = -0.005
        t = np.array([0, 10000, 20000, 30000], dtype=np.int64)
        volt = np.array([16.0, 16.0, 16.1, 16.1])
        cur = np.array([1.0, 2.0, 1.0, 1.0])
        log_r = np.array([0.0, 0.0, 0.0, 0.0])
        norm = np.array([0.5, 0.5, 0.4, 0.4])
        pred = np.array([16.0, 16.0, 15.98, 16.0])
        rec = tc.recover_raw(t, volt, cur, log_r, norm, pred, 4)
        self.assertEqual(len(rec["rows"]), 1)
        k, r = rec["rows"][0]
        self.assertEqual(k, 1)
        self.assertAlmostEqual(r, -0.005, places=9)
        self.assertEqual(rec["at_logged_zero"], 1)
        self.assertEqual(rec["at_logged_zero_below_tol"], 1)


class Attribution(unittest.TestCase):
    def case(self, d7, d8, v8, n_cells=None):
        t = np.arange(10, dtype=np.int64) * 10000
        cur = np.array([1.0] * 8 + [2.0, 2.0])
        volt = np.array([16.0] * 8 + [v8, v8])
        r7 = 0.0011 + d7
        r8 = r7 + d8
        tr = {"r_raw": np.array([0.005, 0.004, 0.003, 0.002, 0.0015, 0.0012, 0.0011, r7, r8, r8 - 0.0001]),
              "d_raw": np.array([0, -0.001, -0.001, -0.001, -0.0005, -0.0003, -0.0001, d7, d8, -0.0001]),
              "accepted": np.array([True] * 10)}
        onset = int(np.flatnonzero(tr["r_raw"] < 0)[0])
        self.assertEqual(onset, 8)
        return tc.attribute(t, volt, cur, tr, 0, onset, n_cells)

    def test_mechanism_b(self):
        # k = 7 quiet (-0.6 mOhm); k = 8 a 1 A step with voltage up (-0.7 mOhm): quiet 0.6 / 1.3
        a = self.case(-0.0006, -0.0007, 16.01)
        self.assertEqual(a["descent_start"], 6)
        self.assertEqual(a["updates"], 2)
        self.assertAlmostEqual(a["quiet_share"], 0.4615, places=4)
        self.assertEqual(a["wrong_way_share_of_steps"], 1.0)
        self.assertEqual(a["mechanism"], "B")
        self.assertIsNone(a["flat_share"])

    def test_mechanism_c(self):
        a = self.case(-0.0006, -0.0007, 15.99)
        self.assertEqual(a["wrong_way_share_of_steps"], 0.0)
        self.assertEqual(a["mechanism"], "C")

    def test_mechanism_a(self):
        # quiet -1.0 mOhm, step -0.3 mOhm: quiet share 10 / 13
        a = self.case(-0.0010, -0.0003, 16.01)
        self.assertAlmostEqual(a["quiet_share"], 0.7692, places=4)
        self.assertEqual(a["mechanism"], "A")


class Flat(unittest.TestCase):
    """NOTE-trace-align.md: flat if |dV| < 0.25 * n_cells * max(R_before, 1 mOhm) * |dI|."""

    case = Attribution.case

    def test_flat_step_is_mechanism_c_but_counts_for_t4(self):
        # k = 8: 1 A step, voltage unchanged. R_before = 0.5 mOhm -> 1 mOhm floor; threshold
        # 0.25 * 4 * 0.001 * 1 = 1 mV; |dV| = 0 -> flat, not wrong-way. Share 0.7 / 1.3 = 0.5385
        a = self.case(-0.0006, -0.0007, 16.0, 4)
        self.assertEqual(a["mechanism"], "C")
        row = [r for r in a["rows"] if r["k"] == 8][0]
        self.assertTrue(row["flat"])
        self.assertFalse(row["wrong_way"])
        self.assertAlmostEqual(row["r_before_mohm"], 0.5, places=9)
        self.assertAlmostEqual(a["flat_share"], 0.5385, places=4)
        self.assertAlmostEqual(a["flat_or_wrong_share"], 0.5385, places=4)
        self.assertEqual(a["wrong_way_share"], 0.0)
        self.assertFalse([r for r in a["rows"] if r["k"] == 7][0]["flat"])   # quiet is never flat

    def test_wrong_way_is_not_flat(self):
        # |dV| = 10 mV >= 1 mV: not flat, but wrong-way; the union is still 0.5385
        a = self.case(-0.0006, -0.0007, 16.01, 4)
        self.assertEqual(a["flat_share"], 0.0)
        self.assertAlmostEqual(a["flat_or_wrong_share"], 0.5385, places=4)
        self.assertAlmostEqual(a["wrong_way_share"], 0.5385, places=4)

    def test_threshold_uses_r_before(self):
        # dV = -4 mV for a 1 A step. With R_before 5.1 mOhm the threshold is 0.25 * 4 * 0.0051 =
        # 5.1 mV -> flat; with R_before 0.5 mOhm (floor 1 mOhm) it is 1 mV -> not flat
        a = self.case(0.004, -0.006, 15.996, 4)
        self.assertEqual(a["descent_start"], 7)
        self.assertEqual(a["updates"], 1)
        self.assertAlmostEqual(a["rows"][0]["r_before_mohm"], 5.1, places=9)
        self.assertTrue(a["rows"][0]["flat"])
        self.assertEqual(a["flat_share"], 1.0)
        b = self.case(-0.0006, -0.0007, 15.996, 4)
        self.assertEqual(b["flat_share"], 0.0)
        self.assertEqual(b["flat_or_wrong_share"], 0.0)

    def test_is_flat(self):
        self.assertTrue(tc.is_flat(0.0009, 1.0, 0.0, 4))
        self.assertFalse(tc.is_flat(0.001, 1.0, 0.0, 4))
        self.assertTrue(tc.is_flat(-0.0199, -2.0, 0.01, 4))      # 0.25 * 4 * 0.01 * 2 = 20 mV
        self.assertFalse(tc.is_flat(-0.0201, -2.0, 0.01, 4))


class StepOffsets(unittest.TestCase):
    def test_aligned(self):
        t, volt, cur = one_step(50, 50)
        so = tc.step_offsets(t, volt, cur)
        self.assertEqual(so["steps"], 1)
        r = so["rows"][0]
        self.assertEqual((r["k"], r["kc"], r["kv"], r["offset"]), (50, 50, 50, 0))
        self.assertEqual(so["histogram"], {"0": 1})
        self.assertAlmostEqual(r["di_a"], 5.0, places=9)
        self.assertAlmostEqual(r["s_since_boot"], 0.5, places=9)

    def test_current_late_is_positive(self):
        t, volt, cur = one_step(52, 50)
        so = tc.step_offsets(t, volt, cur)
        self.assertEqual(so["rows"][0]["offset"], 2)
        self.assertEqual(so["rows"][0]["kv"], 50)

    def test_voltage_late_is_negative(self):
        t, volt, cur = one_step(50, 51)
        self.assertEqual(tc.step_offsets(t, volt, cur)["rows"][0]["offset"], -1)

    def test_step_down(self):
        t, volt, cur = one_step(50, 51, down=True)
        r = tc.step_offsets(t, volt, cur)["rows"][0]
        self.assertLess(r["di_a"], 0)
        self.assertGreater(r["dv_v"], 0)
        self.assertEqual(r["offset"], -1)

    def test_voltage_never_moves(self):
        t, volt, cur = one_step(50, None)
        so = tc.step_offsets(t, volt, cur)
        self.assertIsNone(so["rows"][0]["offset"])
        self.assertEqual(so["histogram"], {"none": 1})

    def test_voltage_outside_window(self):
        t, volt, cur = one_step(50, 57)
        self.assertIsNone(tc.step_offsets(t, volt, cur)["rows"][0]["offset"])
        t, volt, cur = one_step(50, 55)
        self.assertEqual(tc.step_offsets(t, volt, cur)["rows"][0]["offset"], -5)

    def test_mask_and_gap(self):
        t, volt, cur = one_step(50, 50)
        self.assertEqual(tc.step_offsets(t, volt, cur, mask=t < 400000)["steps"], 0)
        t2 = t.copy()
        t2[50:] += 1000000
        self.assertEqual(tc.step_offsets(t2, volt, cur)["steps"], 0)

    def test_small_change_is_not_a_step(self):
        t, volt, cur = one_step(50, 50)
        cur = np.where(np.arange(len(t)) >= 50, 1.9, 1.0)
        self.assertEqual(tc.step_offsets(t, volt, cur)["steps"], 0)


class T5(unittest.TestCase):
    def test_current_late_holds(self):
        t, volt, cur = one_step(50, 49)
        p = tc.t5_check(t, volt, cur, None, 51)
        self.assertTrue(p["graded"])
        self.assertTrue(p["held"])
        self.assertEqual(p["step"]["offset"], 1)
        self.assertEqual(p["step"]["samples_before_onset"], 1)

    def test_aligned_fails(self):
        t, volt, cur = one_step(50, 50)
        p = tc.t5_check(t, volt, cur, None, 50)
        self.assertTrue(p["graded"])
        self.assertFalse(p["held"])

    def test_step_too_early_is_not_graded(self):
        t, volt, cur = one_step(50, 50)
        self.assertFalse(tc.t5_check(t, volt, cur, None, 54)["graded"])
        self.assertTrue(tc.t5_check(t, volt, cur, None, 53)["graded"])

    def test_no_voltage_change_is_not_graded(self):
        t, volt, cur = one_step(50, None)
        p = tc.t5_check(t, volt, cur, None, 50)
        self.assertFalse(p["graded"])
        self.assertIsNone(p["held"])


class Lag(unittest.TestCase):
    def test_voltage_two_samples_late(self):
        rng = np.random.default_rng(5)
        cur = rng.uniform(0.0, 5.0, 500)
        volt = np.full(500, 16.0)
        volt[2:] = 16.0 - 0.05 * cur[:-2]
        prof, best = tc.lag_profile(volt, cur)
        self.assertEqual(best, 2)
        self.assertLess(prof[2], -0.99)


class Analyse(unittest.TestCase):
    def test_collapsing_log(self):
        t, volt, cur = synth(slope=0.02)
        fw = firmware(t, volt, cur)
        rep = tc.analyse(bat_of(t, volt, cur, fw), 4)
        self.assertNotIn("stop", rep)
        self.assertEqual(rep["anchor"]["index"], 200)
        self.assertTrue(rep["gates"]["G2_anchor"])
        self.assertTrue(rep["gates"]["G3_replay"])
        self.assertTrue(rep["collapsed"])
        self.assertTrue(rep["onset_matches"])
        self.assertIn(rep["attribution"]["mechanism"], ("A", "B", "C"))
        self.assertEqual(rep["gaps"], 0)
        self.assertEqual(rep["nominal_dt_ms"], 10.0)
        self.assertTrue(rep["T4"]["graded"])
        self.assertIsInstance(rep["T4"]["held"], bool)
        self.assertIn(rep["T5"]["graded"], (True, False))
        self.assertEqual(rep["step_offsets"]["scope"], "whole log (no takeoff time in the log)")
        json.dumps(rep, default=tc.plain)
        tc.summary_lines(rep)

    def test_healthy_log(self):
        t, volt, cur = synth()
        fw = firmware(t, volt, cur)
        status = (np.array([0, 9000000]), np.array([1, 2]))
        land = (np.array([0, 10000000]), np.array([1, 0]))
        rep = tc.analyse(bat_of(t, volt, cur, fw), 4, status, land)
        self.assertTrue(rep["gates"]["G3_replay"])
        self.assertEqual(rep["armed_s_since_boot"], 9.0)
        self.assertEqual(rep["takeoff_s_since_boot"], 10.0)
        self.assertIn("lag_ground", rep)
        so = rep["step_offsets"]
        self.assertEqual(so["scope"], "ground and the first 5 s after takeoff")
        # the 11 steps between 0.8 and 2.3 A (samples 625..875), plus the jump to flight current
        self.assertGreaterEqual(so["steps"], 12)
        self.assertTrue(all(r["s_since_boot"] < 15.0 for r in so["rows"]))
        if not rep["collapsed"]:
            self.assertFalse(rep["T4"]["graded"])
            self.assertFalse(rep["T5"]["graded"])
        json.dumps(rep, default=tc.plain)
        tc.summary_lines(rep)

    def test_missing_fields(self):
        rep = tc.analyse({"timestamp": np.arange(5)}, 4)
        self.assertIn("voltage_prediction", rep["stop"])
        tc.summary_lines(rep)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name.
"""Tests for gate_props.py: structural properties of PX4's battery-resistance RLS gate (stdlib unittest)."""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gate_props as gp  # noqa: E402

N_CELLS = 4
RAMP = 300
HOLD = 3000
I_HOLD = 12.0


def ramp_then_hold_currents():
    """0.5 A rising to 15 A over RAMP samples, then a constant I_HOLD for HOLD samples."""
    ramp = [0.5 + 14.5 * k / (RAMP - 1) for k in range(RAMP)]
    return ramp + [I_HOLD] * HOLD


def battery_volts(amps, seed):
    """A plausible 4S pack: 16.4 V OCV, 8.5 mOhm/cell, small noise."""
    rng = random.Random(seed)
    return [16.4 - 0.034 * i + rng.gauss(0.0, 0.02) for i in amps]


def wild_volts(n, seed):
    """Voltages with no relation to the current at all."""
    rng = random.Random(seed)
    return [rng.uniform(14.0, 17.0) for _ in range(n)]


def snapshot(est):
    """Everything except the OCV state e0."""
    return (est.e1, est.p00, est.p01, est.p10, est.p11, est.norm, est.r_pub)


class TestConstants(unittest.TestCase):
    def test_initial_norm_matches_px4_log(self):
        # PX4 logs estimation_covariance_norm = 6.01331854 for a 4S pack before any update.
        est = gp.Rls(N_CELLS, 16.2)
        self.assertAlmostEqual(est.norm, 6.01331854, delta=1e-5)
        self.assertEqual(est.r_pub, gp.R_DEFAULT)
        self.assertAlmostEqual(est.e1, 0.02, delta=1e-12)

    def test_zero_current_is_not_an_update(self):
        est = gp.Rls(N_CELLS, 16.2)
        before = snapshot(est) + (est.e0,)
        self.assertIsNone(est.step(16.0, 0.0))
        self.assertEqual(snapshot(est) + (est.e0,), before)


class TestGate(unittest.TestCase):
    def setUp(self):
        self.amps = ramp_then_hold_currents()

    def test_p1_acceptance_ignores_voltage(self):
        _, a = gp.run(battery_volts(self.amps, 1), self.amps, N_CELLS, 16.4)
        _, b = gp.run(wild_volts(len(self.amps), 2), self.amps, N_CELLS, 15.0)
        self.assertEqual([r["accepted"] for r in a], [r["accepted"] for r in b])
        self.assertTrue(any(r["accepted"] for r in a))
        self.assertTrue(any(not r["accepted"] for r in a))

    def test_p2_norm_is_a_running_minimum(self):
        est = gp.Rls(N_CELLS, 16.4)
        last = est.norm
        for v, i in zip(battery_volts(self.amps, 3), self.amps):
            est.step(v, i)
            self.assertLessEqual(est.norm, last)
            last = est.norm

    def test_p3_rejection_reanchors_ocv(self):
        est = gp.Rls(N_CELLS, 16.4)
        rejected = 0
        for v, i in zip(battery_volts(self.amps, 4), self.amps):
            r = est.step(v, i)
            if not r["accepted"]:
                rejected += 1
                self.assertAlmostEqual(est.predict(i), v, delta=1e-9)
        self.assertGreater(rejected, 0)

    def test_p4_rejection_leaves_r_and_covariance_unchanged(self):
        est = gp.Rls(N_CELLS, 16.4)
        rejected = 0
        for v, i in zip(wild_volts(len(self.amps), 5), self.amps):
            before = snapshot(est)
            r = est.step(v, i)
            if not r["accepted"]:
                rejected += 1
                self.assertEqual(snapshot(est), before)
        self.assertGreater(rejected, 0)

    def test_p5_constant_current_freezes_the_estimate(self):
        # Bound: for a unit u orthogonal to x, ||P|| >= u'Pu >= 1/(LAMBDA^k * u'J0u), with u'J0u <= 226/(1-LAMBDA)
        # = 4520 (|x|^2 <= 1 + 15^2) and every accepted norm < 6.0134, so at most 199 consecutive updates at one
        # current can be accepted.
        est = gp.Rls(N_CELLS, 16.4)
        volts = battery_volts(self.amps[:RAMP], 6) + wild_volts(HOLD, 7)
        results = [est.step(v, i) for v, i in zip(volts, self.amps)]
        hold = results[RAMP:]
        first_reject = next(k for k, r in enumerate(hold) if not r["accepted"])
        self.assertLessEqual(first_reject, 250)
        self.assertFalse(any(r["accepted"] for r in hold[first_reject:]))

    def test_p5_frozen_r_ignores_any_voltage(self):
        est = gp.Rls(N_CELLS, 16.4)
        for v, i in zip(battery_volts(self.amps, 8), self.amps):
            est.step(v, i)
        frozen = snapshot(est)
        for v in wild_volts(1000, 9):
            r = est.step(v, I_HOLD)
            self.assertFalse(r["accepted"])
        self.assertEqual(snapshot(est), frozen)

    def test_implied_r_from_logged_values(self):
        est = gp.Rls(N_CELLS, 16.4)
        for v, i in zip(battery_volts(self.amps, 10), self.amps):
            est.step(v, i)
        r = est.step(15.9, I_HOLD)
        self.assertFalse(r["accepted"])
        e1_before = est.e1
        nxt = est.step(15.8, I_HOLD - 1.0)
        implied = gp.implied_pack_r(15.9, I_HOLD, nxt["prediction"], I_HOLD - 1.0)
        self.assertAlmostEqual(implied, e1_before, delta=1e-9)


class TestLockedNegative(unittest.TestCase):
    """Two co-moving samples drive the signed R negative; then no voltage at that current can undo it.

    Hand arithmetic (float64, rounded): after (16.0 V, 1.0 A) the norm is 1.31928 and e1 = 0.018912 Ohm.
    At (17.5 V, 4.0 A), a step that looks like -0.5 Ohm per pack, the candidate norm is 1.29745 (accepted) and
    e1 becomes -0.32091 Ohm (published 0). The next candidate at 4.0 A has norm 1.36007 > 1.29745 (rejected),
    so every later 4.0 A sample is rejected the same way.
    """

    def test_negative_r_published_as_zero_and_locked(self):
        est = gp.Rls(N_CELLS, 16.0)
        self.assertTrue(est.step(16.0, 1.0)["accepted"])
        self.assertAlmostEqual(est.norm, 1.31928, delta=2e-4)
        self.assertTrue(est.step(17.5, 4.0)["accepted"])
        self.assertAlmostEqual(est.norm, 1.29745, delta=2e-4)
        self.assertAlmostEqual(est.e1, -0.32091, delta=1e-3)
        self.assertEqual(est.r_pub, 0.0)
        locked = snapshot(est)
        for v in wild_volts(HOLD, 11):
            self.assertFalse(est.step(v, 4.0)["accepted"])
        self.assertEqual(snapshot(est), locked)
        self.assertEqual(est.r_pub, 0.0)


class TestImpliedR(unittest.TestCase):
    def test_formula(self):
        # e0 = 15.0 + R*10 after a rejection at (15.0 V, 10 A); prediction at 9 A = 15.0 + R*(10 - 9).
        self.assertAlmostEqual(gp.implied_pack_r(15.0, 10.0, 14.9, 9.0), -0.1, delta=1e-12)
        with self.assertRaises(ValueError):
            gp.implied_pack_r(15.0, 10.0, 14.9, 10.0)


if __name__ == "__main__":
    unittest.main()

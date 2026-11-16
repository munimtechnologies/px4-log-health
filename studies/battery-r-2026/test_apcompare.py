# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name.
"""Tests for apcompare.py. Every expected number below was worked out by hand (see comments)."""

import csv
import io
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apcompare as apc  # noqa: E402


def base(**kw):
    # V_filt 16.0 V, I_filt 1.0 A, R 0.02 Ohm, V_ref 16.2 V at I_ref 0.5 A, I_max 10 A, last call at 1000 ms
    args = dict(voltage_filt=16.0, current_filt=1.0, resistance=0.02, voltage_ref=16.2, current_ref=0.5,
                current_max=10.0, timer_ms=1000)
    args.update(kw)
    return apc.APResistance(**args)


class OneCall(unittest.TestCase):
    def test_positive_candidate_is_blended(self):
        # dI = 2, dt = 0.1 s -> alpha = 0.1/0.6 = 1/6; w = 0.1*2/10 = 0.02; r = 0.2/2 = 0.1
        # R = 0.02*0.98 + 0.1*0.02 = 0.0216; cap (16.2-15.8)/(3-0.5) = 0.16 does not bind
        e = base()
        self.assertEqual(e.update(1100, 15.8, 3.0), "blend")
        self.assertAlmostEqual(e.last_r, 0.1, places=12)
        self.assertAlmostEqual(e.resistance, 0.0216, places=12)
        self.assertFalse(e.last_capped)
        self.assertAlmostEqual(e.voltage_filt, 16.0 - 0.2 / 6.0, places=12)  # 15.96666...
        self.assertAlmostEqual(e.current_filt, 1.0 + 2.0 / 6.0, places=12)  # 1.33333...
        self.assertEqual(e.timer_ms, 1100)
        self.assertEqual(e.voltage_ref, 16.2)
        self.assertEqual(e.current_max, 10.0)

    def test_co_moving_step_is_discarded(self):
        # voltage up with current up: r = -(0.1)/2 = -0.05 -> dropped; cap 0.1/2.5 = 0.04 > 0.02
        e = base()
        self.assertEqual(e.update(1100, 16.1, 3.0), "discard")
        self.assertAlmostEqual(e.last_r, -0.05, places=12)
        self.assertEqual(e.resistance, 0.02)
        self.assertFalse(e.last_capped)
        self.assertAlmostEqual(e.voltage_filt, 16.0 + 0.1 / 6.0, places=12)

    def test_cap_binds(self):
        # R = 0.2*0.98 + 0.1*0.02 = 0.198, then capped to 0.4/2.5 = 0.16
        e = base(resistance=0.2)
        self.assertEqual(e.update(1100, 15.8, 3.0), "blend")
        self.assertAlmostEqual(e.resistance, 0.16, places=12)
        self.assertTrue(e.last_capped)

    def test_no_step_only_moves_current_max(self):
        e = base(current_filt=3.0, current_max=2.0)
        self.assertEqual(e.update(1100, 15.0, 3.0), "no_step")
        self.assertEqual(e.current_max, 3.0)
        self.assertEqual(e.timer_ms, 1000)
        self.assertEqual(e.voltage_filt, 16.0)
        self.assertEqual(e.resistance, 0.02)
        self.assertEqual(e.voltage_ref, 16.2)
        self.assertIsNone(e.last_r)

    def test_no_current_changes_nothing(self):
        for cur in (0.0, -1.0, 1e-9, float("nan")):
            e = base(current_max=2.0)
            self.assertEqual(e.update(1100, 15.0, cur), "no_current")
            self.assertEqual(e.current_max, 2.0)
            self.assertEqual(e.timer_ms, 1000)
            self.assertEqual(e.resistance, 0.02)

    def test_new_voltage_reference(self):
        # 16.3 > 16.2: reference moves to (16.3 V, 3 A); r = -0.3/2 = -0.15 -> dropped; no cap (V == V_ref)
        e = base()
        self.assertEqual(e.update(1100, 16.3, 3.0), "discard")
        self.assertEqual(e.voltage_ref, 16.3)
        self.assertEqual(e.current_ref, 3.0)
        self.assertAlmostEqual(e.last_r, -0.15, places=12)
        self.assertEqual(e.resistance, 0.02)
        self.assertFalse(e.last_capped)

    def test_filter_gain_is_clamped_to_half(self):
        # dt = 10 s -> 10/10.5 clamped to 0.5; R = 0 + 0.1*0.02 = 0.002
        e = base(resistance=0.0, timer_ms=0)
        self.assertEqual(e.update(10000, 15.8, 3.0), "blend")
        self.assertAlmostEqual(e.voltage_filt, 15.9, places=12)
        self.assertAlmostEqual(e.current_filt, 2.0, places=12)
        self.assertAlmostEqual(e.resistance, 0.002, places=12)


class Invariants(unittest.TestCase):
    def test_never_negative_and_never_back_to_zero(self):
        rng = random.Random(7)
        e = apc.APResistance()
        t = 10000
        became_positive = False
        for _ in range(5000):
            t += 100
            cur = 0.0 if rng.random() < 0.1 else rng.uniform(0.0, 20.0)
            e.update(t, rng.uniform(14.0, 17.0), cur)
            self.assertGreaterEqual(e.resistance, 0.0)
            if became_positive:
                self.assertGreater(e.resistance, 0.0)
            became_positive = became_positive or e.resistance > 0.0
        self.assertTrue(became_positive)


class Helpers(unittest.TestCase):
    def test_block_means(self):
        rows = [{"t_us": k * 10000, "v": float(k), "i": 2.0 * k, "landed": 1 if k < 15 else 0} for k in range(25)]
        b = apc.block_means(rows)
        self.assertEqual(len(b), 2)
        self.assertEqual(b[0]["t_us"], 90000)
        self.assertAlmostEqual(b[0]["v"], 4.5, places=12)
        self.assertAlmostEqual(b[0]["i"], 9.0, places=12)
        self.assertEqual(b[0]["landed"], 1)
        self.assertEqual(b[1]["t_us"], 190000)
        self.assertAlmostEqual(b[1]["v"], 14.5, places=12)
        self.assertAlmostEqual(b[1]["i"], 29.0, places=12)
        self.assertEqual(b[1]["landed"], 0)

    def test_to_ms(self):
        self.assertEqual(apc.to_ms(-104528177, -104528177, 10000), 10000)
        self.assertEqual(apc.to_ms(-104518174, -104528177, 10000), 10010)

    def test_parse_rows(self):
        text = ("time_from_takeoff_us,voltage_v,current_a,internal_resistance_estimate,estimation_covariance_norm,"
                "voltage_prediction,prediction_error,armed_last_reported,landed_last_reported\n"
                "-20000,16.5,0,0.005,6.0,0,0,0,1\n"
                "10000,15.25,12.5,0,0.2,15.3,-0.05,1,0\n")
        rows = apc.parse_rows(csv.DictReader(io.StringIO(text)))
        self.assertEqual(rows[0], {"t_us": -20000, "v": 16.5, "i": 0.0, "landed": 1, "logged_r": 0.005})
        self.assertEqual(rows[1], {"t_us": 10000, "v": 15.25, "i": 12.5, "landed": 0, "logged_r": 0.0})

    def test_run_from_zero_state(self):
        # s0: dt 10 s -> alpha 0.5; r = -(16-0)/1 = -16 dropped; V_ref = 16 @ 1 A; V_filt 8, I_filt 0.5
        # s1: alpha 1/6; r = -(15.5-8)/4.5 = -1.6667 dropped; cap 0.5/4 = 0.125 not below R = 0
        #     V_filt = 8*5/6 + 15.5/6 = 9.25; I_filt = 0.5*5/6 + 5/6 = 1.25
        s = [{"t_us": 0, "v": 16.0, "i": 1.0, "landed": 1}, {"t_us": 100000, "v": 15.5, "i": 5.0, "landed": 0}]
        tr = apc.run(s)
        self.assertEqual([e["outcome"] for e in tr], ["discard", "discard"])
        self.assertAlmostEqual(tr[0]["r"], -16.0, places=12)
        self.assertAlmostEqual(tr[1]["r"], -7.5 / 4.5, places=12)
        self.assertEqual([e["R"] for e in tr], [0.0, 0.0])
        self.assertEqual([e["landed"] for e in tr], [1, 0])
        self.assertEqual(apc.run([]), [])

    def test_summarize(self):
        tr = [
            {"t_us": -200000, "landed": 1, "outcome": "discard", "r": -1.0, "R": 0.0, "capped": False},
            {"t_us": -100000, "landed": 1, "outcome": "blend", "r": 0.1, "R": 0.04, "capped": False},
            {"t_us": 0, "landed": 0, "outcome": "discard", "r": -0.1, "R": 0.04, "capped": False},
            {"t_us": 100000, "landed": 0, "outcome": "blend", "r": 0.2, "R": 0.048, "capped": True},
            {"t_us": 200000, "landed": 0, "outcome": "no_current", "r": None, "R": 0.048, "capped": False},
            {"t_us": 300000, "landed": 1, "outcome": "blend", "r": 0.1, "R": 0.05, "capped": False},
        ]
        s = apc.summarize(tr, 4)
        self.assertEqual(s["calls"], 6)
        self.assertEqual(s["outcomes_all"], {"no_current": 1, "no_step": 0, "blend": 3, "discard": 2})
        self.assertEqual(s["first_positive_t_us"], -100000)
        self.assertEqual(s["first_airborne_t_us"], 0)
        self.assertTrue(s["positive_before_takeoff"])
        self.assertEqual(s["airborne_n"], 3)
        self.assertEqual(s["airborne_positive_n"], 3)
        self.assertEqual(s["airborne_discards"], 1)
        self.assertEqual(s["airborne_blends"], 1)
        self.assertEqual(s["discard_share_airborne"], 0.5)
        self.assertEqual(s["airborne_capped_n"], 1)
        self.assertAlmostEqual(s["airborne_mohm_per_cell_median"], 12.0, places=9)
        self.assertAlmostEqual(s["airborne_mohm_per_cell_min"], 10.0, places=9)
        self.assertAlmostEqual(s["airborne_mohm_per_cell_max"], 12.0, places=9)
        self.assertAlmostEqual(s["airborne_mohm_per_cell_final"], 12.0, places=9)
        self.assertEqual(s["airborne_mohm_per_cell_at"], {"+10s": None, "+20s": None, "+30s": None})

    def test_summarize_empty(self):
        s = apc.summarize([], 4)
        self.assertFalse(s["positive_before_takeoff"])
        self.assertIsNone(s["discard_share_airborne"])
        self.assertIsNone(s["airborne_mohm_per_cell_median"])


if __name__ == "__main__":
    unittest.main()

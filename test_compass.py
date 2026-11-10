"""Synthetic tests for compass.py (no flight log needed). By MV2 of Munim, Inc.; written with AI
agents. Apache-2.0."""

import unittest

import numpy as np

import compass as cp


def flight(slope, current_fn, n=500, landed=0, with_battery=True):
    """5 Hz compass and battery samples for n samples; norm = 0.5 + slope * current."""
    t = np.arange(n, dtype=np.int64) * 200000
    cur = np.array([current_fn(i) for i in range(n)], dtype=np.float64)
    norm = 0.5 + slope * cur
    zeros = np.zeros(n)
    data = {"vehicle_magnetometer": {0: {"timestamp": t, "magnetometer_ga[0]": norm,
                                         "magnetometer_ga[1]": zeros, "magnetometer_ga[2]": zeros,
                                         "device_id": np.full(n, 0x00070109)}},
            "vehicle_land_detected": {0: {"timestamp": np.array([0], dtype=np.int64),
                                          "landed": np.array([landed])}}}
    if with_battery:
        data["battery_status"] = {0: {"timestamp": t, "current_a": cur}}
    return data


def ramp(i):
    return 5.0 + 0.2 * (i % 50)


class Coupling(unittest.TestCase):
    def test_strong_line(self):
        x = np.linspace(0.0, 20.0, 201)          # p5 = 1.0, p95 = 19.0
        c = cp.coupling(x, 0.5 + 0.005 * x)       # median norm 0.55
        self.assertAlmostEqual(c["pct"], 16.364, places=3)   # 0.005 * 18 / 0.55 * 100
        self.assertAlmostEqual(c["r"], 1.0, places=4)
        self.assertEqual(cp.classify(c), "strong")

    def test_moderate_and_weak(self):
        x = np.linspace(0.0, 20.0, 201)
        c = cp.coupling(x, 0.5 + 0.002 * x)       # 0.036 / 0.52 * 100 = 6.923
        self.assertAlmostEqual(c["pct"], 6.923, places=3)
        self.assertEqual(cp.classify(c), "moderate")
        c = cp.coupling(x, 0.5 + 0.0001 * x)      # 0.0018 / 0.501 * 100 = 0.359
        self.assertAlmostEqual(c["pct"], 0.359, places=3)
        self.assertEqual(cp.classify(c), "weak")

    def test_no_fit_on_constant_current(self):
        c = cp.coupling(np.full(10, 3.0), np.linspace(0.4, 0.5, 10))
        self.assertIsNone(c["pct"])
        self.assertIsNone(cp.classify(c))


class Helpers(unittest.TestCase):
    def test_airborne_mask(self):
        mask, src = cp.airborne_mask([-10, 50, 150, 250], ([0, 100, 200], [1, 0, 1]))
        self.assertEqual(list(mask), [False, False, True, False])
        self.assertEqual(src, "land_detected")
        mask, src = cp.airborne_mask([5, 15], None, ([0, 10], [1, 2]))
        self.assertEqual(list(mask), [False, True])
        self.assertEqual(src, "armed")
        self.assertEqual(cp.airborne_mask([1], None, None), (None, "none"))

    def test_paired(self):
        keep, v = cp.paired([0.5e6, 1.5e6, 3e6], [0, 1e6, 2e6], [0, 10, 20])
        self.assertEqual(list(keep), [True, True, False])
        self.assertEqual(list(v[:2]), [5.0, 15.0])

    def test_fleet_place(self):
        self.assertEqual(cp.fleet_place(0.359), "below p10")
        self.assertEqual(cp.fleet_place(0.507), "p10-p25")
        self.assertEqual(cp.fleet_place(3.0), "p25-p50")
        self.assertEqual(cp.fleet_place(16.364), "p75-p90")
        self.assertEqual(cp.fleet_place(98.1), "above p90")
        self.assertIsNone(cp.fleet_place(None))

    def test_bus_and_id(self):
        self.assertEqual(cp.bus_name(0x00070109), "i2c")
        self.assertEqual(cp.most_common_id([0, 5, 7, 7]), 7)
        self.assertIsNone(cp.most_common_id([0, 0]))


class Measure(unittest.TestCase):
    def test_strong_flight(self):
        m = cp.measure_data(flight(0.01, ramp))
        self.assertTrue(m["measurable"])
        self.assertEqual(m["pairs"], 500)
        c = m["coupling"]
        self.assertAlmostEqual(c["slope_ga"], 0.01, places=6)
        self.assertEqual((c["x_p5"], c["x_p95"], c["x_median"]), (5.4, 14.4, 9.9))
        self.assertAlmostEqual(c["pct"], 15.025, places=2)   # 0.01 * 9.0 / 0.599 * 100
        self.assertEqual(m["class"], "strong")
        self.assertEqual(m["bus"], "i2c")
        level, text = cp.verdict(m)
        self.assertEqual(level, "flag")
        self.assertIn(cp.GUIDE, text)
        self.assertIn("p75-p90", text)
        self.assertNotIn("after PX4's power compensation", text)
        self.assertIn("after PX4's power compensation", cp.verdict(m, 2)[1])

    def test_weak_flight(self):
        m = cp.measure_data(flight(0.0002, ramp))
        self.assertEqual(m["class"], "weak")
        self.assertEqual(cp.verdict(m)[0], "ok")

    def test_not_measurable(self):
        m = cp.measure_data(flight(0.01, lambda i: 0.2 + 0.001 * (i % 2)))
        self.assertFalse(m["measurable"])
        self.assertEqual(m["why_not"], "median airborne current under 0.5 A")
        self.assertEqual(cp.verdict(m)[0], "not checked")
        m = cp.measure_data(flight(0.01, lambda i: 10.0 + 0.1 * (i % 20)))
        self.assertEqual(m["why_not"], "current range under 5 A")
        m = cp.measure_data(flight(0.01, ramp, with_battery=False))
        self.assertEqual(m["why_not"], "no valid battery current")
        m = cp.measure_data(flight(0.01, ramp, landed=1))
        self.assertEqual(m["why_not"], "fewer than 150 airborne pairs")
        m = cp.measure_data({})
        self.assertEqual(m["read"], "no vehicle_magnetometer data")
        self.assertEqual(cp.verdict(m)[0], "not checked")


if __name__ == "__main__":
    unittest.main()

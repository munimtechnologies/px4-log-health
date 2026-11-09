"""Synthetic tests for mag_logs.py (px4-log-health study magcurrent-2026). No network, no real log.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import os
import tempfile
import unittest

import numpy as np

import mag_logs as ml


def _measured(pct, r, cls, ratio=None, external=None, bus="i2c"):
    return {"read": "ok", "measurable": True, "class": cls, "external": external, "bus": bus,
            "mag_test_ratio_median": ratio,
            "coupling": {"pct": pct, "r": r, "slope_ga": 0.001 if r >= 0 else -0.001}}


class TestCoupling(unittest.TestCase):
    def test_ramp(self):
        x = np.arange(400) * 40.0 / 399.0
        c = ml.coupling(x, 0.5 + 0.001 * x)
        self.assertEqual(c["pairs"], 400)
        self.assertAlmostEqual(c["x_p5"], 2.0, places=3)
        self.assertAlmostEqual(c["x_p95"], 38.0, places=3)
        self.assertAlmostEqual(c["median_ga"], 0.52, places=4)
        self.assertAlmostEqual(c["slope_ga"], 0.001, places=6)
        self.assertAlmostEqual(c["r"], 1.0, places=4)
        self.assertAlmostEqual(c["pct"], 6.923, places=3)

    def test_flat_current_has_no_fit(self):
        c = ml.coupling(np.full(200, 3.0), np.linspace(0.4, 0.5, 200))
        self.assertIsNone(c["pct"])
        self.assertIsNone(c["r"])

    def test_classify(self):
        self.assertEqual(ml.classify({"pct": 12.0, "r": 0.6}), "strong")
        self.assertEqual(ml.classify({"pct": 12.0, "r": 0.4}), "moderate")
        self.assertEqual(ml.classify({"pct": 6.0, "r": -0.35}), "moderate")
        self.assertEqual(ml.classify({"pct": 4.0, "r": 0.9}), "weak")
        self.assertIsNone(ml.classify({"pct": None, "r": None}))
        self.assertIsNone(ml.classify(None))


class TestTimes(unittest.TestCase):
    def test_airborne_from_land_detected(self):
        t = [5e6, 10e6, 20e6, 49e6, 50e6, 60e6]
        m, src = ml.airborne_mask(t, ([0, 10e6, 50e6], [1, 0, 1]), ([0], [2]))
        self.assertEqual(src, "land_detected")
        self.assertEqual(list(m), [False, True, True, True, False, False])

    def test_before_first_state_is_not_airborne(self):
        m, _ = ml.airborne_mask([1e6, 3e6], ([2e6], [0]))
        self.assertEqual(list(m), [False, True])

    def test_airborne_from_armed(self):
        m, src = ml.airborne_mask([1e6, 6e6], None, ([0, 5e6], [1, 2]))
        self.assertEqual(src, "armed")
        self.assertEqual(list(m), [False, True])

    def test_no_state(self):
        self.assertEqual(ml.airborne_mask([1e6], None, None), (None, "none"))

    def test_paired(self):
        keep, v = ml.paired([-1e5, 5e5, 1.5e6, 2e6, 3e6], [0, 1e6, 2e6], [0, 10, 20])
        self.assertEqual(list(keep), [False, True, True, True, False])
        self.assertAlmostEqual(float(v[1]), 5.0)
        self.assertAlmostEqual(float(v[2]), 15.0)

    def test_paired_gap(self):
        keep, _ = ml.paired([1.5e6], [0, 3e6], [0, 30])
        self.assertEqual(list(keep), [False])


class TestIds(unittest.TestCase):
    DEV = (0x0D << 16) | (0x1E << 8) | (1 << 3) | 1

    def test_dev_value(self):
        self.assertEqual(self.DEV, 859657)
        self.assertEqual(ml.bus_name(self.DEV), "i2c")
        self.assertEqual(ml.bus_name(3), "uavcan")

    def test_most_common_id(self):
        self.assertEqual(ml.most_common_id([0, 5, 7, 7]), 7)
        self.assertIsNone(ml.most_common_id([0, 0]))
        self.assertIsNone(ml.most_common_id(None))

    def test_external_of(self):
        sm = {0: {"device_id": [11, 11], "is_external": [0, 0]},
              1: {"device_id": [self.DEV], "is_external": [1]}}
        self.assertIs(ml.external_of(sm, self.DEV), True)
        self.assertIs(ml.external_of(sm, 11), False)
        self.assertIsNone(ml.external_of(sm, 99))
        self.assertIsNone(ml.external_of(sm, None))


def _log(current, slope=0.003):
    n = 1000
    tm = np.arange(n, dtype=np.int64) * 200000
    norm = 0.4 + slope * np.asarray(current, dtype=np.float64)
    return {
        "vehicle_magnetometer": {0: {"timestamp": tm, "magnetometer_ga[0]": norm,
                                     "magnetometer_ga[1]": np.zeros(n), "magnetometer_ga[2]": np.zeros(n),
                                     "device_id": np.full(n, TestIds.DEV)}},
        "sensor_mag": {0: {"device_id": np.full(5, TestIds.DEV), "is_external": np.ones(5)}},
        "vehicle_land_detected": {0: {"timestamp": np.array([0, 20000000, 180000000]),
                                      "landed": np.array([1, 0, 1])}},
        "estimator_status": {0: {"timestamp": tm, "mag_test_ratio": np.full(n, 0.2)}},
        "battery_status": {0: {"timestamp": tm, "current_a": np.full(n, -1.0)},
                           1: {"timestamp": tm, "current_a": np.asarray(current, dtype=np.float64)}},
    }


class TestMeasure(unittest.TestCase):
    def test_strong(self):
        cur = 5.0 + 30.0 * np.arange(1000) / 999.0
        m = ml.measure_data(_log(cur))
        self.assertEqual(m["read"], "ok")
        self.assertTrue(m["measurable"])
        self.assertEqual(m["class"], "strong")
        self.assertEqual(m["battery_instance"], 1)
        self.assertEqual(m["pairs"], 800)
        self.assertIs(m["external"], True)
        self.assertEqual(m["bus"], "i2c")
        self.assertEqual(m["devtype"], "0x0D")
        self.assertEqual(m["airborne_from"], "land_detected")
        self.assertAlmostEqual(m["airborne_s"], 160.0, places=1)
        self.assertAlmostEqual(m["mag_test_ratio_median"], 0.2, places=4)
        self.assertAlmostEqual(m["coupling"]["median_ga"], 0.46, places=4)
        self.assertAlmostEqual(m["coupling"]["pct"], 14.083, delta=0.01)
        self.assertNotIn("thrust_coupling", m)

    def test_low_current(self):
        m = ml.measure_data(_log(np.full(1000, 0.1)))
        self.assertFalse(m["measurable"])
        self.assertEqual(m["why_not"], "median airborne current under 0.5 A")

    def test_no_valid_current(self):
        d = _log(np.full(1000, 10.0))
        d["battery_status"] = {0: {"timestamp": d["battery_status"][0]["timestamp"],
                                   "current_a": np.full(1000, -1.0)}}
        m = ml.measure_data(d)
        self.assertFalse(m["measurable"])
        self.assertEqual(m["why_not"], "no valid battery current")

    def test_no_mag(self):
        self.assertEqual(ml.measure_data({})["read"], "no vehicle_magnetometer data")


class TestRanks(unittest.TestCase):
    def test_ranks(self):
        self.assertEqual(ml.ranks([5, 1, 5]), [2.5, 1.0, 2.5])

    def test_spearman(self):
        self.assertAlmostEqual(ml.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(ml.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertIsNone(ml.spearman([1], [2]))


class TestList(unittest.TestCase):
    BASE = {"log_id": "a", "log_date": "2026-10-01", "duration_s": 300, "sys_hw": "PX4_FMU_V6X",
            "ver_sw_release": "v1.15.4", "vehicle_uuid": "u1", "mav_type": "Quadrotor"}

    def test_eligible(self):
        self.assertIsNotNone(ml.eligible(dict(self.BASE))[0])
        self.assertEqual(ml.eligible(dict(self.BASE, sys_hw="PX4_SITL"))[1], "simulation")
        self.assertEqual(ml.eligible(dict(self.BASE, log_date="2024-12-31"))[1], "before 2025-01-01")
        self.assertEqual(ml.eligible(dict(self.BASE, duration_s=60))[1], "shorter than 120 s")
        self.assertEqual(ml.eligible(dict(self.BASE, ver_sw_release="v1.12.3"))[1], "older than v1.13")

    def test_build_order(self):
        e = [dict(self.BASE, log_id="b", log_date="2026-09-01"), dict(self.BASE, log_id="c"),
             dict(self.BASE, log_id="d", duration_s=10)]
        cands, counts = ml.build(e)
        self.assertEqual([c["log_id"] for c in cands], ["c", "b"])
        self.assertEqual(counts["eligible"], 2)


class TestRun(unittest.TestCase):
    def test_one_per_vehicle_and_bad_log(self):
        tmp = os.path.join(tempfile.gettempdir(), "mag-test.ulg")
        cands = [{"log_id": "1", "vehicle": "v"}, {"log_id": "2", "vehicle": "v"},
                 {"log_id": "3", "vehicle": "w"}]

        def fetch(log_id, path):
            if log_id == "3":
                with open(path, "wb") as f:
                    f.write(b"x")
                return {"has_mag": True, "has_battery": True}, path
            return {"has_mag": True, "has_battery": False}, None

        def bad(path):
            raise RuntimeError("broken")
        rows = []
        res = ml.run(cands, fetch, bad, rows.append, tmp, sleep=lambda s: None)
        self.assertEqual(res, {"requested": 2, "downloaded": 1, "stop": "list exhausted"})
        self.assertEqual(rows[0]["status"], "no mag or battery topic")
        self.assertEqual(rows[1]["status"], "mag and battery")
        self.assertTrue(rows[1]["measure"]["read"].startswith("error: RuntimeError"))
        self.assertFalse(os.path.exists(tmp))


class TestSummary(unittest.TestCase):
    def test_predictions(self):
        recs = []
        for i, (pct, cls) in enumerate([(1, "weak"), (2, "weak"), (3, "weak"), (12, "strong"),
                                        (4, "weak"), (6, "moderate")]):
            recs.append({"order": i, "params": {"CAL_MAG_COMP_TYP": 0}, "measure": _measured(pct, 0.6, cls)})
        for i in range(4):
            recs.append({"order": 10 + i, "params": {"CAL_MAG_COMP_TYP": 2 if i == 0 else 0},
                         "measure": {"read": "ok", "measurable": False, "why_not": "no valid battery current"}})
        recs.append({"order": 20, "status": "no mag or battery topic", "params": {}})
        s = ml.summarise(recs)
        p = s["predictions"]
        self.assertIs(p["K1"], True)
        self.assertIs(p["K2"], True)
        self.assertIs(p["K3"], True)
        self.assertIs(p["K4"], False)
        self.assertIsNone(p["K5"])
        self.assertIsNone(p["K6"])
        self.assertEqual(s["reported"]["measurable"], 6)
        self.assertEqual(s["reported"]["classes"], {"weak": 4, "moderate": 1, "strong": 1})
        self.assertEqual(s["reported"]["why_not"], {"no valid battery current": 4})
        self.assertEqual(len(s["reported"]["strong_rows"]), 1)

    def test_k5_k6(self):
        recs = []
        for i in range(30):
            recs.append({"order": i, "params": {"CAL_MAG_COMP_TYP": 0},
                         "measure": _measured(float(i), 0.6, "weak", ratio=i / 100.0,
                                              external=(i % 2 == 0))})
        p = ml.summarise(recs)["predictions"]
        self.assertIs(p["K5"], True)
        # internal = odd i (median 15), external = even i (median 14)
        self.assertIs(p["K6"], True)
        self.assertIs(p["K4"], True)


if __name__ == "__main__":
    unittest.main()

"""Tests for health.py and fleet_health.py on synthetic flights (no log, no
network). By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import json
import unittest

import numpy as np

import fleet_health as fh
import health


def synthetic(vib_air=2.0, jam_air=50, spoof_at=50, sat_window=(200, 390), voltage=15.0,
              land=((0, 1), (10, 0), (200, 1)), release=None, jam_state=1):
    """A log as read_health returns: on the ground 0-10 s, flying 10-200 s,
    on the ground to 210 s."""
    land_t = np.array([t for t, _ in land], dtype=float)
    landed = np.array([s for _, s in land])
    t1 = np.arange(211, dtype=float)                    # 1 Hz, 0..210 s
    fly1 = health.flying_at(t1, land_t, landed)
    inc = np.where(np.concatenate([[False], fly1[1:] & fly1[:-1]]), 1, 100)
    inc[0] = 0
    imu = {"timestamp": (t1 * 1e6).astype(np.int64),
           "accel_vibration_metric": np.where(fly1, vib_air, 20.0),
           "accel_clipping[0]": np.cumsum(inc).astype(np.uint32),
           "accel_clipping[1]": np.zeros(211, dtype=np.uint32),
           "accel_clipping[2]": np.zeros(211, dtype=np.uint32)}
    gps = {"timestamp": (t1 * 1e6).astype(np.int64),
           "jamming_indicator": np.where(fly1, jam_air, 0).astype(np.int32),
           "jamming_state": np.ones(211, dtype=np.uint8),
           "spoofing_state": np.ones(211, dtype=np.uint8),
           "satellites_used": np.full(211, 14, dtype=np.uint8),
           "eph": np.full(211, 0.8)}
    gps["jamming_state"][60] = jam_state
    if spoof_at is not None:
        gps["spoofing_state"][spoof_at] = 2
    batt = {"timestamp": (t1 * 1e6).astype(np.int64),
            "warning": np.where((t1 >= 150) & (t1 < 200), 1, 0).astype(np.uint8),
            "connected": np.ones(211, dtype=bool),
            "voltage_v": np.full(211, voltage)}
    batt["warning"][205] = 2                            # on the ground after landing: ignored
    status = {"timestamp": np.array([0, 5, 100, 150], dtype=np.int64) * 1000000,
              "failsafe": np.array([False, False, True, False]),
              "vehicle_type": np.array([1, 1, 1, 1], dtype=np.uint8),
              "is_vtol": np.zeros(4, dtype=bool)}
    t10 = np.arange(2100) / 10.0                        # 10 Hz
    motors = {"timestamp": (t10 * 1e6).astype(np.int64)}
    for k in range(12):
        motors["control[%d]" % k] = np.full(2100, 0.5) if k < 4 else np.full(2100, np.nan)
    if sat_window:
        motors["control[0]"][sat_window[0]:sat_window[1]] = 1.0
    return {"topics": {
        "vehicle_land_detected": {0: {"timestamp": (land_t * 1e6).astype(np.int64), "landed": landed}},
        "vehicle_imu_status": {0: imu},
        "vehicle_gps_position": {0: gps},
        "battery_status": {0: batt},
        "vehicle_status": {0: status},
        "actuator_motors": {0: motors},
    }, "release": release}


class Health(unittest.TestCase):
    def test_flight_seconds(self):
        self.assertAlmostEqual(health.flight_seconds([0, 10, 200], [1, 0, 1], 210), 190.0)
        self.assertAlmostEqual(health.flight_seconds([0, 10], [1, 0], 70), 60.0)

    def test_measure(self):
        r = health.measure_health(synthetic())
        self.assertTrue(r["health_ok"])
        self.assertAlmostEqual(r["flight_s"], 190.0)
        self.assertAlmostEqual(r["vib_median"], 2.0)
        self.assertEqual(r["vib_band"], "green")
        self.assertEqual(r["clip_count"], 189)          # ground steps of 100 are not counted
        self.assertAlmostEqual(r["jam_median"], 50.0)
        self.assertEqual(r["spoof_state_max"], 2)
        self.assertEqual(r["jam_state_max"], 1)
        self.assertEqual(r["sats_median"], 14.0)
        self.assertEqual(r["battery_warning_max"], 1)
        self.assertTrue(r["failsafe_in_flight"])
        self.assertTrue(r["multicopter"])
        self.assertEqual(r["motors"], 4)
        self.assertAlmostEqual(r["sat_share"], 0.1, places=2)
        text = " ".join(r["flags"])
        for part in ("clipped 189", "jamming indicator median 50", "spoofing", "battery warning level 1",
                     "failsafe", "full command"):
            self.assertIn(part, text)
        self.assertNotIn("vibration", text)
        self.assertEqual(r["notes"], [])
        json.dumps(r)

    def test_vibration_bands(self):
        self.assertEqual(health.vib_band(4.9), "green")
        self.assertEqual(health.vib_band(6.0), "orange")
        self.assertEqual(health.vib_band(9.81), "orange")
        self.assertEqual(health.vib_band(12.0), "red")
        r = health.measure_health(synthetic(vib_air=6.0))
        self.assertEqual(r["vib_band"], "orange")
        self.assertIn("vibration orange", " ".join(r["flags"]))

    def test_release_and_units(self):
        self.assertEqual(health.release_name(0x010E03FF), "v1.14.3")
        self.assertEqual(health.release_name(0x010E0300), "v1.14.3-0")
        self.assertIsNone(health.release_name(None))
        self.assertIsNone(health.release_name(0))
        self.assertEqual(health.vib_units("v1.12.3"), "m/s")
        self.assertEqual(health.vib_units("v1.11.3-0"), "m/s")
        self.assertEqual(health.vib_units("v1.13.0-0"), "m/s^2")
        self.assertEqual(health.vib_units("v1.16.0-128"), "m/s^2")
        for unknown in (None, "v0.0.0", "v5.10.0", "v1", "custom"):
            self.assertIsNone(health.vib_units(unknown), unknown)

    def test_old_release_is_not_banded(self):
        r = health.measure_health(synthetic(vib_air=6.0, release="v1.12.3"))
        self.assertEqual(r["vib_units"], "m/s")
        self.assertIsNone(r["vib_band"])
        self.assertNotIn("vibration", " ".join(r["flags"]))
        self.assertIn("not banded", " ".join(r["notes"]))
        new = health.measure_health(synthetic(vib_air=6.0, release="v1.14.3"))
        self.assertEqual((new["vib_units"], new["vib_band"]), ("m/s^2", "orange"))
        unknown = health.measure_health(synthetic(vib_air=6.0))
        self.assertEqual(unknown["vib_band"], "orange")
        self.assertIn("assumed", unknown["vib_units"])

    def test_jamming_state_two_is_noted_three_is_flagged(self):
        warn = health.measure_health(synthetic(jam_air=0, jam_state=2))
        self.assertEqual(warn["jam_state_max"], 2)
        self.assertNotIn("reported jamming", " ".join(warn["flags"]))
        self.assertIn("interference", " ".join(warn["notes"]))
        crit = health.measure_health(synthetic(jam_air=0, jam_state=3))
        self.assertIn("reported jamming (critical)", " ".join(crit["flags"]))

    def test_quiet_log(self):
        log = synthetic(jam_air=0, spoof_at=None, sat_window=None)
        r = health.measure_health(log)
        self.assertFalse(r["jam_reported"])
        self.assertNotIn("jam_median", r)
        self.assertEqual(r["spoof_state_max"], 1)
        self.assertEqual(r["sat_share"], 0.0)

    def test_unmonitored_battery(self):
        r = health.measure_health(synthetic(voltage=0.0))
        self.assertNotIn("battery_warning_max", r)

    def test_short_flight(self):
        r = health.measure_health(synthetic(land=((0, 1), (10, 0), (50, 1))))
        self.assertFalse(r["health_ok"])
        self.assertIn("under 60 s", r["health_reason"])

    def test_no_land_detected(self):
        log = synthetic()
        del log["topics"]["vehicle_land_detected"]
        self.assertFalse(health.measure_health(log)["health_ok"])

    def test_not_multicopter(self):
        log = synthetic()
        log["topics"]["vehicle_status"][0]["vehicle_type"][:] = 2
        r = health.measure_health(log)
        self.assertFalse(r["multicopter"])
        self.assertNotIn("sat_share", r)


class Place(unittest.TestCase):
    Q = {"n": 100, "p10": 1.0, "p25": 2.0, "p50": 3.0, "p75": 4.0, "p90": 5.0, "p99": 6.0}

    def test_place(self):
        self.assertEqual(health.place(0.5, self.Q), "below p10")
        self.assertEqual(health.place(1.0, self.Q), "p10-p25")
        self.assertEqual(health.place(3.5, self.Q), "p50-p75")
        self.assertEqual(health.place(5.99, self.Q), "p90-p99")
        self.assertEqual(health.place(6.0, self.Q), "above p99")
        self.assertIsNone(health.place(None, self.Q))
        self.assertIsNone(health.place(1.0, None))

    def test_tied_cuts_name_the_whole_range(self):
        # clip_per_min in baseline.json: zero from p10 to p75
        q = {"n": 198, "p10": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "p90": 24.9, "p99": 2131.0}
        self.assertEqual(health.place(0.0, q), "p10-p75 (tied)")
        self.assertEqual(health.place(0, q), "p10-p75 (tied)")
        self.assertEqual(health.place(3.0, q), "p75-p90")
        self.assertEqual(health.place(24.9, q), "p90-p99")
        self.assertEqual(health.place(3000.0, q), "above p99")
        q2 = dict(self.Q, p25=3.0)                      # p25 = p50 = 3
        self.assertEqual(health.place(3.0, q2), "p25-p50 (tied)")
        self.assertEqual(health.place(2.0, q2), "p10-p25")
        self.assertEqual(health.place(1.0, self.Q), "p10-p25")   # one equal cut is not a tie

    def test_fleet_place(self):
        base = {"fields": {"vib_median": self.Q, "flight_s": self.Q}}
        r = health.measure_health(synthetic(release="v1.14.3"))
        self.assertEqual(health.fleet_place(r, base), {"vib_median": "p25-p50", "flight_s": "above p99"})
        old = health.measure_health(synthetic(release="v1.12.3"))
        self.assertNotIn("vib_median", health.fleet_place(old, base))
        self.assertIsNone(health.fleet_place({"health_ok": False}, base))
        self.assertIsNone(health.fleet_place(r, None))
        tied = {"fields": {"sat_share": {"p10": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "p90": 0.0101}}}
        quiet = health.measure_health(synthetic(sat_window=None))
        self.assertEqual(health.fleet_place(quiet, tied), {"sat_share": "p10-p75 (tied)"})


def row(i, vib=2.0, clip=0, jam=10.0, batt=0, fs=False, sat=0.0, lag=None, vehicle=True, release="v1.14.3"):
    r = {"log_id": "log%d" % i, "vehicle": ("v%d" % i) if vehicle else None, "download": "ok",
         "health_ok": True, "vib_median": vib, "vib_band": health.vib_band(vib), "clip_count": clip,
         "jam_median": jam, "jam_p95": jam, "jam_state_reported": True, "jam_state_max": 1,
         "spoof_state_reported": True, "spoof_state_max": 1, "battery_warning_max": batt,
         "failsafe_in_flight": fs, "sat_share": sat, "flight_s": 300.0, "mav_type": "Quadrotor"}
    r["flags"] = health.flags(r)
    r["lag"] = lag or {"passed": False, "reason": "too little manoeuvring", "ver_sw_release": release}
    return r


class Fleet(unittest.TestCase):
    def test_draw_excludes_and_takes_one_per_vehicle(self):
        cands = [{"log_id": "a", "vehicle": "v1"}, {"log_id": "b", "vehicle": "v1"},
                 {"log_id": "c", "vehicle": "v2"}, {"log_id": "d", "vehicle": "v3"},
                 {"log_id": "e", "vehicle": None}]
        got = fh.draw(cands, {"d"}, {"v2"})
        ids = sorted(c["log_id"] for c in got)
        self.assertEqual(len(ids), 2)
        self.assertIn("e", ids)
        self.assertTrue(ids[0] in ("a", "b"))
        self.assertEqual(fh.draw(cands, set(), set()), fh.draw(cands, set(), set()))

    def test_summarise(self):
        rows = [row(i) for i in range(140)]
        rows += [row(140 + i, vib=6.0, clip=5) for i in range(20)]       # loud, clipping
        rows += [row(160 + i, batt=1, fs=True) for i in range(20)]
        rows += [row(180 + i, lag={"passed": True, "reason": "ok", "lag_ms": 100.0 + i % 7,
                                   "se_ms": 1.0, "param_gps_delay_ms": 110.0,
                                   "lag_minus_param_ms": -10.0 + i % 7, "gps_bus": "serial"})
                 for i in range(70)]
        rows.append({"log_id": "x", "vehicle": "vx", "download": "too large", "health_ok": False,
                     "health_reason": "download too large", "lag": {"passed": False}})
        s = fh.summarise(rows)
        json.dumps(s)
        self.assertEqual(s["health_ok"], 250)
        c = s["checks"]
        self.assertTrue(c["E1"])
        self.assertTrue(c["P1"])                         # 20/250 = 8%
        self.assertTrue(c["P2"])
        self.assertTrue(c["P3"])                         # 8% clip
        self.assertTrue(c["P4"])                         # loud 100% vs quiet 0%
        self.assertTrue(c["P5"])
        self.assertTrue(c["P8"])                         # 8%
        self.assertTrue(c["P9"])
        self.assertTrue(c["P10"])
        self.assertTrue(c["R1"])
        self.assertFalse(c["R2"])                        # no log is 50 ms off
        self.assertTrue(s["decision"]["B1_publish_baseline"])
        self.assertFalse(s["decision"]["R_replicated"])
        self.assertEqual(s["not_ok_reasons"], {"download too large": 1})
        self.assertEqual(s["replication"]["passed"], 70)

    def test_too_few(self):
        s = fh.summarise([row(i) for i in range(20)])
        self.assertFalse(s["checks"]["E1"])
        self.assertIsNone(s["checks"]["P1"])
        self.assertIsNone(s["checks"]["R1"])

    def test_baseline(self):
        b = fh.baseline([row(i, vib=float(i)) for i in range(11)])
        self.assertEqual(b["fields"]["vib_median"]["p50"], 5.0)
        self.assertEqual(b["fields"]["vib_median"]["n"], 11)
        self.assertEqual(b["vib_band"]["red"], 1)
        json.dumps(b)

    def test_baseline_vibration_only_from_v1_13_on(self):
        rows = [row(i, vib=8.0) for i in range(10)]
        rows += [row(10 + i, vib=0.02, release="v1.12.3") for i in range(10)]
        rows += [row(20 + i, vib=0.03, release=None) for i in range(5)]
        rows[-1]["release"] = "v1.15.0"                  # health.py 0.2.0 rows carry it themselves
        b = fh.baseline(rows)
        self.assertEqual(b["fields"]["vib_median"]["n"], 11)
        self.assertEqual(b["fields"]["flight_s"]["n"], 25)
        self.assertEqual(b["vib_band"], {"green": 1, "orange": 10, "red": 0})
        self.assertEqual(fh.row_release({"lag": {"ver_sw_release": "v1.12.3"}}), "v1.12.3")
        e = fh.explore(rows)
        self.assertEqual(e["by_vib_units"]["m/s"]["n"], 10)

    def test_rescore_round_trip(self):
        import os
        import tempfile
        rows = [row(i) for i in range(5)]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "logs.jsonl")
            with open(p, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            self.assertEqual(fh.main(["--out-dir", d, "--rescore", p]), 0)
            with open(os.path.join(d, "summary.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["health_ok"], 5)


if __name__ == "__main__":
    unittest.main()

"""Tests for compare.py 0.2.0 on synthetic checkup rows (no logs, no numpy needed).

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""
import copy
import unittest

import compare as cm


def row(lag=110.0, se=1.0, d=0.0, status="ok", implied=5.0, implied_se=2.0, vib=3.0, units="m/s^2",
        clip=0, flight=300.0, sat=0.01, compass="weak", pct=4.0, collapsed=False, warn=0, failsafe=False,
        sats=14.0, eph=0.6, release="v1.16.0"):
    clip_per_min = round(clip / (flight / 60.0), 3)
    return {
        "log": "x.ulg", "checkup": "0.3.9",
        "health": {"health_ok": True, "flight_s": flight, "release": release, "vib_median": vib,
                   "vib_units": units, "vib_band": "green" if vib < 4.905 else "orange",
                   "clip_count": clip, "clip_per_min": clip_per_min, "sat_share": sat,
                   "battery_warning_max": warn, "failsafe_in_flight": failsafe, "sats_median": sats,
                   "eph_median": eph, "flags": []},
        "gps": {"lag": {"passed": True}, "innov": {"passed": True, "implied_excess_ms": implied,
                                                   "implied_se_ms": implied_se}},
        "gps_verdict": {"lag_ms": lag, "se_ms": se, "param_ms": lag - d, "d_ms": d, "status": status},
        "gps_dynmodel": None,
        "compass": {"level": "ok", "class": compass, "coupling_pct": pct},
        "battery": {"level": "ok", "collapsed": collapsed},
    }


def item(result, name):
    return next(i for i in result["items"] if i["item"] == name)


class TestSame(unittest.TestCase):
    def test_version(self):
        self.assertEqual(cm.__version__, "0.2.0")

    def test_identical_rows_change_nothing(self):
        r = row()
        out = cm.compare_rows(r, copy.deepcopy(r), {"A": 1}, {"A": 1})
        self.assertEqual(out["worse"], [])
        self.assertEqual(out["better"], [])
        self.assertEqual(out["verdict"], "no change beyond the rules")
        self.assertEqual(out["params"], {"settings": [], "learned": [], "volatile": []})
        self.assertFalse(out["firmware"]["changed"])
        self.assertEqual(out["compared"], 10)
        self.assertEqual(out["coverage"], {"compared": 10, "checks": 10, "not_comparable": [], "complete": True})
        for i in out["items"]:
            self.assertEqual(i["status"], "same", i)


class TestGpsLag(unittest.TestCase):
    def test_small_change_is_same(self):
        out = cm.compare_rows(row(lag=110.0, d=0.0), row(lag=139.0, d=29.0))
        self.assertEqual(item(out, "gps_lag_ms")["status"], "same")
        self.assertEqual(item(out, "gps_lag_ms")["delta"], 29.0)

    def test_large_change_away_from_setting_is_worse(self):
        out = cm.compare_rows(row(lag=110.0, d=0.0), row(lag=150.0, d=40.0))
        self.assertEqual(item(out, "gps_lag_ms")["status"], "worse")
        self.assertIn("gps_lag_ms", out["worse"])

    def test_large_change_toward_setting_is_better(self):
        # model 6 -> 7 like the upgrade-2026 pairs: 200 ms with a 110 ms setting, then 105 ms
        out = cm.compare_rows(row(lag=200.0, d=90.0, status="off"), row(lag=105.0, d=-5.0, status="ok"))
        self.assertEqual(item(out, "gps_lag_ms")["status"], "better")
        self.assertEqual(item(out, "gps_lag_ms")["delta"], -95.0)
        self.assertEqual(item(out, "gps_delay_status")["status"], "better")
        self.assertEqual(out["worse"], [])
        self.assertEqual(out["verdict"], "changed, nothing worse")

    def test_noisy_lag_needs_two_se(self):
        # 40 ms change but SE 20 and 20: combined 28.3, 2 SE = 56.6 > 40
        out = cm.compare_rows(row(lag=110.0, se=20.0, d=0.0), row(lag=150.0, se=20.0, d=40.0))
        self.assertEqual(item(out, "gps_lag_ms")["status"], "same")

    def test_missing_se_on_a_large_change_is_not_comparable(self):
        # 0.1.0 took the missing SE as 0 and called this 'worse'
        out = cm.compare_rows(row(lag=110.0, se=None, d=0.0), row(lag=150.0, d=40.0))
        i = item(out, "gps_lag_ms")
        self.assertEqual(i["status"], "not comparable")
        self.assertEqual(i["delta"], 40.0)
        self.assertIn("no standard error on the before log", i["why"])
        self.assertEqual(out["worse"], [])
        self.assertEqual(out["coverage"]["not_comparable"], ["gps_lag_ms"])
        self.assertEqual(out["verdict"], "no change beyond the rules (only 9 of 10 checks comparable)")

    def test_zero_or_nan_se_is_missing(self):
        for bad in (0.0, -1.0, float("nan"), "n/a"):
            out = cm.compare_rows(row(lag=110.0, d=0.0), row(lag=150.0, se=bad, d=40.0))
            i = item(out, "gps_lag_ms")
            self.assertEqual(i["status"], "not comparable", bad)
            self.assertIn("the after log", i["why"])

    def test_missing_se_on_a_small_change_is_same(self):
        # the rule needs both conditions to fire, so a 15 ms change is 'same' whatever the SE
        out = cm.compare_rows(row(lag=110.0, se=None, d=0.0), row(lag=125.0, se=None, d=15.0))
        i = item(out, "gps_lag_ms")
        self.assertEqual(i["status"], "same")
        self.assertIn("no standard error on both logs", i["why"])
        self.assertTrue(out["coverage"]["complete"])

    def test_not_measured(self):
        b = row()
        b["gps_verdict"] = {"status": "not measured", "reason": "hover"}
        out = cm.compare_rows(b, row())
        self.assertEqual(item(out, "gps_lag_ms")["status"], "not comparable")
        self.assertEqual(item(out, "gps_delay_status")["status"], "not comparable")
        self.assertEqual(out["coverage"]["not_comparable"], ["gps_lag_ms", "gps_delay_status"])
        self.assertEqual(out["compared"], 8)
        self.assertEqual(out["verdict"], "no change beyond the rules (only 8 of 10 checks comparable)")

    def test_status_ok_to_off_is_worse(self):
        out = cm.compare_rows(row(lag=150.0, d=40.0, status="ok"), row(lag=165.0, d=55.0, status="off"))
        self.assertEqual(item(out, "gps_lag_ms")["status"], "same")
        self.assertEqual(item(out, "gps_delay_status")["status"], "worse")


class TestInnov(unittest.TestCase):
    def test_innov_grows(self):
        out = cm.compare_rows(row(implied=-10.0), row(implied=-45.0))
        i = item(out, "gps_innov_excess_ms")
        self.assertEqual(i["status"], "worse")
        self.assertEqual(i["delta"], 35.0)

    def test_innov_shrinks(self):
        out = cm.compare_rows(row(implied=60.0), row(implied=20.0))
        self.assertEqual(item(out, "gps_innov_excess_ms")["status"], "better")

    def test_innov_not_passed(self):
        a = row()
        a["gps"]["innov"] = {"passed": False, "reason": "too few samples"}
        out = cm.compare_rows(row(), a)
        self.assertEqual(item(out, "gps_innov_excess_ms")["status"], "not comparable")

    def test_innov_missing_se(self):
        out = cm.compare_rows(row(implied=-10.0, implied_se=None), row(implied=-45.0))
        i = item(out, "gps_innov_excess_ms")
        self.assertEqual(i["status"], "not comparable")
        self.assertEqual(i["delta"], 35.0)
        out = cm.compare_rows(row(implied=-10.0), row(implied=-45.0, implied_se=float("nan")))
        self.assertEqual(item(out, "gps_innov_excess_ms")["status"], "not comparable")
        out = cm.compare_rows(row(implied=-10.0, implied_se=None), row(implied=-20.0, implied_se=None))
        self.assertEqual(item(out, "gps_innov_excess_ms")["status"], "same")


class TestVibration(unittest.TestCase):
    def test_up_half_is_worse(self):
        out = cm.compare_rows(row(vib=3.0), row(vib=4.6))
        self.assertEqual(item(out, "vibration_median")["status"], "worse")

    def test_up_half_but_tiny_is_same(self):
        out = cm.compare_rows(row(vib=0.4), row(vib=0.8))
        self.assertEqual(item(out, "vibration_median")["status"], "same")

    def test_down_a_third_is_better(self):
        out = cm.compare_rows(row(vib=6.0), row(vib=3.9))
        self.assertEqual(item(out, "vibration_median")["status"], "better")

    def test_old_units(self):
        out = cm.compare_rows(row(units="m/s"), row())
        self.assertEqual(item(out, "vibration_median")["status"], "not comparable")


class TestOtherItems(unittest.TestCase):
    def test_clipping_appears(self):
        out = cm.compare_rows(row(clip=0), row(clip=12))
        self.assertEqual(item(out, "clipping_per_min")["status"], "worse")

    def test_clipping_rate(self):
        self.assertEqual(item(cm.compare_rows(row(clip=10), row(clip=20)), "clipping_per_min")["status"], "same")
        self.assertEqual(item(cm.compare_rows(row(clip=10), row(clip=30)), "clipping_per_min")["status"], "worse")
        self.assertEqual(item(cm.compare_rows(row(clip=30), row(clip=10)), "clipping_per_min")["status"], "better")
        self.assertEqual(item(cm.compare_rows(row(clip=5), row(clip=0)), "clipping_per_min")["status"], "better")

    def test_saturation(self):
        self.assertEqual(item(cm.compare_rows(row(sat=0.01), row(sat=0.06)), "motor_saturation_share")["status"],
                         "worse")
        self.assertEqual(item(cm.compare_rows(row(sat=0.01), row(sat=0.05)), "motor_saturation_share")["status"],
                         "same")
        self.assertEqual(item(cm.compare_rows(row(sat=0.20), row(sat=0.10)), "motor_saturation_share")["status"],
                         "better")

    def test_compass(self):
        self.assertEqual(item(cm.compare_rows(row(compass="weak"), row(compass="strong")), "compass_coupling")["status"],
                         "worse")
        self.assertEqual(item(cm.compare_rows(row(compass="moderate"), row(compass="weak")),
                              "compass_coupling")["status"], "better")
        b = row()
        b["compass"] = {"level": "not checked", "class": None}
        self.assertEqual(item(cm.compare_rows(b, row()), "compass_coupling")["status"], "not comparable")

    def test_battery_collapse(self):
        self.assertEqual(item(cm.compare_rows(row(collapsed=False), row(collapsed=True)),
                              "battery_r_collapse")["status"], "worse")
        self.assertEqual(item(cm.compare_rows(row(collapsed=True), row(collapsed=False)),
                              "battery_r_collapse")["status"], "better")
        self.assertEqual(item(cm.compare_rows(row(collapsed=None), row(collapsed=True)),
                              "battery_r_collapse")["status"], "not comparable")

    def test_warning_and_failsafe(self):
        out = cm.compare_rows(row(warn=0, failsafe=False), row(warn=1, failsafe=True))
        self.assertEqual(item(out, "battery_warning")["status"], "worse")
        self.assertEqual(item(out, "failsafe_in_flight")["status"], "worse")
        self.assertEqual(out["verdict"], "worse on 2 items")

    def test_worse_with_missing_data_says_so(self):
        a = row(warn=1)
        a["compass"] = {"level": "not checked", "class": None}
        out = cm.compare_rows(row(), a)
        self.assertEqual(out["verdict"], "worse on 1 item (only 9 of 10 checks comparable)")

    def test_environment_never_counts(self):
        out = cm.compare_rows(row(sats=16.0, eph=0.5), row(sats=9.0, eph=1.2))
        self.assertEqual(item(out, "satellites_median")["status"], "worse")
        self.assertEqual(item(out, "eph_median")["status"], "worse")
        self.assertEqual(out["worse"], [])
        self.assertEqual(out["verdict"], "no change beyond the rules")

    def test_health_not_read(self):
        b = row()
        b["health"] = {"health_ok": False, "health_reason": "under 60 s in the air"}
        out = cm.compare_rows(b, row())
        self.assertEqual(item(out, "vibration_median")["status"], "not comparable")
        self.assertEqual(item(out, "battery_warning")["status"], "not comparable")
        self.assertEqual(out["compared"], 5)
        self.assertEqual(out["coverage"]["not_comparable"],
                         ["vibration_median", "clipping_per_min", "motor_saturation_share", "battery_warning",
                          "failsafe_in_flight"])
        self.assertEqual(out["verdict"], "no change beyond the rules (only 5 of 10 checks comparable)")

    def test_nothing_comparable(self):
        empty = {"health": {"health_ok": False}, "gps_verdict": {}, "gps": {}, "compass": {}, "battery": {}}
        out = cm.compare_rows(empty, copy.deepcopy(empty))
        self.assertEqual(out["compared"], 0)
        self.assertEqual(out["verdict"], "not comparable")
        self.assertEqual(out["coverage"]["checks"], 10)


class TestParams(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(cm.param_class("LND_FLIGHT_T_LO"), "volatile")
        self.assertEqual(cm.param_class("CAL_GYRO0_XOFF"), "learned")
        self.assertEqual(cm.param_class("CAL_MAG1_ZOFF"), "learned")
        self.assertEqual(cm.param_class("CAL_BARO0_OFF"), "learned")
        self.assertEqual(cm.param_class("CAL_ACC0_TEMP"), "learned")
        self.assertEqual(cm.param_class("EKF2_MAG_DECL"), "learned")
        self.assertEqual(cm.param_class("CAL_MAG0_ID"), "setting")
        self.assertEqual(cm.param_class("CAL_MAG0_XSCALE"), "setting")
        self.assertEqual(cm.param_class("EKF2_GPS_DELAY"), "setting")
        self.assertEqual(cm.param_class("SENS_BOARD_X_OFF"), "setting")

    def test_float32_tolerance(self):
        self.assertTrue(cm.same_value(0.004999999888241291, 0.005))
        self.assertFalse(cm.same_value(110.0, 160.0))
        self.assertTrue(cm.same_value(7, 7))
        self.assertFalse(cm.same_value(6, 7))

    def test_diff(self):
        b = {"EKF2_GPS_DELAY": 110.0, "GPS_UBX_DYNMODEL": 6, "CAL_GYRO0_XOFF": 0.01, "LND_FLIGHT_T_LO": 5,
             "OLD": 1}
        a = {"EKF2_GPS_DELAY": 110.0, "GPS_UBX_DYNMODEL": 7, "CAL_GYRO0_XOFF": 0.02, "LND_FLIGHT_T_LO": 9,
             "NEW": 2}
        d = cm.param_diff(b, a)
        self.assertEqual([p["name"] for p in d["settings"]], ["GPS_UBX_DYNMODEL", "NEW", "OLD"])
        self.assertEqual(d["settings"][1], {"name": "NEW", "before": None, "after": 2})
        self.assertEqual([p["name"] for p in d["learned"]], ["CAL_GYRO0_XOFF"])
        self.assertEqual([p["name"] for p in d["volatile"]], ["LND_FLIGHT_T_LO"])
        self.assertIsNone(cm.param_diff(None, a))


class TestRender(unittest.TestCase):
    def test_render(self):
        out = cm.compare_rows(row(lag=200.0, d=90.0, status="off", release="v1.16.0"),
                              row(lag=105.0, d=-5.0, status="ok", release="v1.17.0", flight=1200.0),
                              {"GPS_UBX_DYNMODEL": 6}, {"GPS_UBX_DYNMODEL": 7})
        self.assertTrue(out["firmware"]["changed"])
        self.assertEqual(len(out["notes"]), 1)
        md = cm.render(out, "a.ulg", "b.ulg")
        self.assertIn("**Verdict: changed, nothing worse.**", md)
        self.assertIn("Checks compared: 10 of 10 vehicle checks.", md)
        self.assertIn("v1.16.0 -> v1.17.0 (changed)", md)
        self.assertIn("1 setting differs between the two logs:", md)
        self.assertIn("- GPS_UBX_DYNMODEL: 6 -> 7", md)
        self.assertIn("| gps_lag_ms | 200.0 | 105.0 | -95.0 | better", md)
        self.assertIn("made with AI agents", md)
        self.assertIn(cm.PHYSICAL_CAVEAT, md)
        self.assertIn("compare 0.2.0", md)

    def test_render_missing(self):
        out = cm.compare_rows(row(lag=110.0, se=None, d=0.0), row(lag=150.0, d=40.0))
        md = cm.render(out)
        self.assertIn("**Verdict: no change beyond the rules (only 9 of 10 checks comparable).**", md)
        self.assertIn("Checks compared: 9 of 10 vehicle checks; not comparable (missing or unusable data, "
                      "never counted as a pass): gps_lag_ms.", md)
        self.assertIn("| gps_lag_ms | 110.0 | 150.0 | 40.0 | not comparable: changed by more than 30 ms", md)

    def test_plain_row(self):
        self.assertEqual(cm.plain_row({"a": 1, "b": [True, None]}), {"a": 1, "b": [True, None]})


class TestExitCode(unittest.TestCase):
    def test_exit_codes(self):
        self.assertEqual(cm.exit_code({"worse": ["x"], "compared": 10}, fail_on_worse=True), 1)
        self.assertEqual(cm.exit_code({"worse": ["x"], "compared": 10}), 0)
        self.assertEqual(cm.exit_code({"worse": [], "compared": 5}, min_compared=8), 2)
        self.assertEqual(cm.exit_code({"worse": [], "compared": 8}, min_compared=8), 0)
        self.assertEqual(cm.exit_code({"worse": ["x"], "compared": 5}, fail_on_worse=True, min_compared=8), 1)


if __name__ == "__main__":
    unittest.main()

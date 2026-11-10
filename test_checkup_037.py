"""Tests for checkup 0.3.7: PX4 main logs get a GPS delay verdict (px4-gps-lag 0.2.0 reads
vehicle_gnss), and when a DroneCAN receiver's own time stamps set the delay EKF2 used (PX4 PR
#27427, v1.18 and main), the report says that the delay parameter was ignored.

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""
import json
import os
import unittest
from unittest import mock

import checkup

HERE = os.path.dirname(os.path.abspath(__file__))
T = [1000000 + 100000 * k for k in range(9)]
MAIN = {"SENS_GNSS0_DELAY": 110, "SENS_GNSS1_DELAY": 110, "SENS_GNSS0_ID": 0, "SENS_GNSS1_ID": 0}
# PX4 device ids: bus type in bits 0-2 (3 = UAVCAN/DroneCAN, 5 = serial), devtype in bits 16-23
CAN = (0x85 << 16) | (0x7D << 8) | 3
SERIAL = (0xA5 << 16) | 5
PIN = "f0b39b3eff9819c0e9cbbed5d23e3d50a639696b"
OLD_PIN = "967b5ad92d04ca6ac7e29684b411fbf5df243e05"


def inputs(params, delay_us, dev, topic="vehicle_gnss"):
    return {"params": params, "topic": topic, "stamps": list(T),
            "samples": [t - delay_us for t in T], "device_ids": [dev] * len(T)}


def lag(lag_ms, passed=True, reason="ok"):
    return {"passed": passed, "reason": reason, "lag_ms": lag_ms, "se_ms": 1.5,
            "param_gps_delay_ms": None, "lag_minus_param_ms": None}


def inn(x):
    return {"passed": True, "reason": "ok", "implied_excess_ms": x}


def report(lag_row, inn_row, applied):
    return {"log": "/logs/main.ulg", "checkup": checkup.__version__,
            "health": {"health_ok": False, "health_reason": "under 60 s in the air"},
            "gps": {"lag": lag_row, "innov": inn_row},
            "gps_verdict": checkup.gps_verdict(lag_row, inn_row, applied)}


class Version(unittest.TestCase):
    def test_version(self):
        self.assertEqual(checkup.__version__, "0.3.9")


class DroneCAN(unittest.TestCase):
    def test_self_stamped_receiver_is_named_and_gets_no_suggestion(self):
        a = checkup.applied_delay(**inputs(MAIN, 85600, CAN))
        self.assertEqual((a["applied_ms"], a["param"], a["source"], a["device_id"]),
                         (85.6, "SENS_GNSS0_DELAY", "other", CAN))
        v = checkup.gps_verdict(lag(166.3), inn(40.0), a)
        self.assertEqual((v["status"], v["innov"], v["suggest_ms"]), ("off", "agrees", None))
        self.assertAlmostEqual(v["d_ms"], 80.7, places=6)
        self.assertEqual(v["delay_set_by"], "DroneCAN receiver")
        self.assertIn("PR #27427", v["no_suggestion"])
        self.assertIn("SENS_GNSSn_DELAY", v["no_suggestion"])
        self.assertNotIn("PPS", v["no_suggestion"])
        json.dumps(v)
        md = checkup.render(report(lag(166.3), inn(40.0), a))
        self.assertIn("EKF2 fused this log's GPS samples 86 ms before they arrived, a shift the DroneCAN "
                      "receiver's own time stamps set, not SENS_GNSSn_DELAY (+81 ms).", md)
        self.assertIn("so there is no parameter to suggest", md)
        self.assertNotIn("Suggestion", md)

    def test_within_50_ms(self):
        a = checkup.applied_delay(**inputs(MAIN, 98000, CAN))
        v = checkup.gps_verdict(lag(110.3), inn(5.0), a)
        self.assertEqual((v["status"], v["suggest_ms"], v["delay_set_by"]), ("ok", None, "DroneCAN receiver"))
        md = checkup.render(report(lag(110.3), inn(5.0), a))
        self.assertIn("a shift the DroneCAN receiver's own time stamps set, not SENS_GNSSn_DELAY (+12 ms).", md)
        self.assertIn("nothing to change", md)

    def test_on_its_parameter_a_dronecan_receiver_gets_a_suggestion(self):
        # a node without its own GNSS time falls back to the parameter; then the parameter matters
        a = checkup.applied_delay(**inputs(MAIN, 110000, CAN))
        v = checkup.gps_verdict(lag(175.0), inn(31.0), a)
        self.assertEqual((v["delay_set_by"], v["status"], v["suggest_ms"], v["param_name"]),
                         ("parameter", "off", 175, "SENS_GNSS0_DELAY"))

    def test_other_buses_keep_the_pps_wording(self):
        a = checkup.applied_delay(**inputs(MAIN, 95000, SERIAL))
        v = checkup.gps_verdict(lag(175.0), inn(40.0), a)
        self.assertEqual((v["delay_set_by"], v["suggest_ms"]), ("PPS or GPS driver", None))
        self.assertIn("PPS", v["no_suggestion"])
        md = checkup.render(report(lag(175.0), inn(40.0), a))
        self.assertIn("a shift set by PPS or the GPS driver rather than SENS_GNSSn_DELAY (+80 ms).", md)
        self.assertNotIn("DroneCAN", md)

    def test_old_firmware_has_no_delay_set_by(self):
        v = checkup.gps_verdict({"passed": True, "reason": "ok", "lag_ms": 175.0, "se_ms": 1.0,
                                 "param_gps_delay_ms": 110.0, "lag_minus_param_ms": 65.0}, inn(31.0), None)
        self.assertNotIn("delay_set_by", v)
        self.assertEqual(v["suggest_ms"], 175)


class MainLogs(unittest.TestCase):
    def check(self, gps, delay_inputs):
        with mock.patch.object(checkup, "run_health",
                               return_value={"health_ok": False, "health_reason": "under 60 s in the air"}), \
                mock.patch.object(checkup, "run_gps", return_value=gps), \
                mock.patch.object(checkup, "read_delay_inputs", return_value=delay_inputs):
            return checkup.check("main.ulg", None)

    def test_a_measured_main_log_gets_a_verdict(self):
        r = self.check({"lag": lag(189.5), "innov": inn(30.0)}, inputs(MAIN, 110000, SERIAL))
        v = r["gps_verdict"]
        self.assertEqual((v["status"], v["suggest_ms"], v["param_name"], v["delay_set_by"]),
                         ("off", 190, "SENS_GNSS0_DELAY", "parameter"))
        json.dumps(r)

    def test_a_real_reason_is_kept(self):
        r = self.check({"lag": lag(None, passed=False, reason="too little manoeuvring"), "innov": None},
                       inputs(MAIN, 110000, SERIAL))
        v = r["gps_verdict"]
        self.assertEqual((v["status"], v["reason"]), ("not measured", "too little manoeuvring"))

    def test_an_old_gps_lag_is_still_explained(self):
        r = self.check({"lag": lag(None, passed=False, reason="error: no vehicle_gps_position"), "innov": None},
                       inputs(MAIN, 110000, SERIAL))
        self.assertIn("px4-gps-lag 0.2.0 does", r["gps_verdict"]["reason"])
        self.assertIn("Not measured: this log uses PX4's newer vehicle_gnss topic", checkup.render(r))


class Pins(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(HERE, name), encoding="utf-8") as f:
            return f.read()

    def test_fetch_deps_takes_gps_lag_020(self):
        import fetch_deps
        self.assertEqual(fetch_deps.PIN, PIN)
        self.assertIn(("gps_lag.py", "0.2.0"), fetch_deps.FILES)

    def test_the_action_uses_the_same_commit(self):
        for name in ("action.yml", "action.sh"):
            text = self.read(name)
            self.assertIn(PIN, text, name)
            self.assertNotIn(OLD_PIN, text, name)


if __name__ == "__main__":
    unittest.main()

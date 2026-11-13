"""Synthetic tests for mcloss_census.py (no logs, no network, no pyulog). By MV2 of Munim, Inc.; written with
AI agents. Apache-2.0."""

import hashlib
import unittest

import mcloss_census as c


class D:
    def __init__(self, name, multi_id, data):
        self.name, self.multi_id, self.data = name, multi_id, data


class FakeULog:
    def __init__(self, data_list, last_timestamp, params=None):
        self.data_list, self.last_timestamp = data_list, last_timestamp
        self.initial_parameters = params or {}


BASE = {"log_id": "abc", "vehicle_uuid": "0123456789abcdef", "duration_s": 300, "mav_type": "Ground Rover",
        "ver_sw_release": "v1.14.3 255", "sys_hw": "PX4_FMU_V6C", "source": "webui", "type": "personal",
        "flight_modes": [2, 5, "x"]}


class TestSelection(unittest.TestCase):
    def test_compact_keeps(self):
        e = c.compact(BASE)
        self.assertEqual(e["key"], hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(e["v"], c.vehicle_hash("0123456789abcdef"))
        self.assertEqual(e["modes"], [2, 5])
        self.assertTrue(e["E"])
        self.assertEqual(e["mav"], "Ground Rover")
        self.assertEqual(e["dur"], 300)

    def test_compact_drops(self):
        for change in ({"ver_sw_release": "v1.13.3 255"}, {"duration_s": 119}, {"sys_hw": "SITL"},
                       {"source": "CI"}, {"type": "Simulation"}, {"vehicle_uuid": "000000000000"},
                       {"log_id": ""}, {"ver_sw_release": "garbage"}):
            self.assertIsNone(c.compact(dict(BASE, **change)), change)

    def test_enriched(self):
        self.assertFalse(c.compact(dict(BASE, flight_modes=[3, 5]))["E"])
        self.assertFalse(c.compact(dict(BASE, flight_modes=[2, 3]))["E"])
        self.assertFalse(c.compact(dict(BASE, flight_modes=None))["E"])
        self.assertTrue(c.compact(dict(BASE, flight_modes=[15, 18]))["E"])
        self.assertTrue(c.compact(dict(BASE, flight_modes=["10", "12"]))["E"])

    def test_release_series(self):
        self.assertEqual(c.release_series("v1.16.0 255"), ((1, 16), "release"))
        self.assertEqual(c.release_series("v1.14.0 0"), ((1, 14), "dev"))
        self.assertIsNone(c.release_series(""))

    def test_pick(self):
        es = [{"v": "a", "key": "5", "E": False, "log_id": "a5"}, {"v": "a", "key": "2", "E": False, "log_id": "a2"},
              {"v": "a", "key": "7", "E": True, "log_id": "a7"}, {"v": "b", "key": "1", "E": True, "log_id": "b1"},
              {"v": "c", "key": "3", "E": False, "log_id": "c3"}]
        self.assertEqual([e["log_id"] for e in c.pick(es)], ["b1", "a2", "c3"])
        self.assertEqual([e["log_id"] for e in c.pick(es, enriched_only=True)], ["b1", "a7"])

    def test_plan_order(self):
        self.assertEqual(c.plan_order(list("abcd"), list("xy")),
                         [("R", "a"), ("R", "b"), ("R", "c"), ("E", "x"), ("R", "d"), ("E", "y")])
        self.assertEqual(c.plan_order([], ["x"]), [("E", "x")])
        self.assertEqual(c.plan_order([], []), [])


class TestExtract(unittest.TestCase):
    def test_extract(self):
        ul = FakeULog([
            D("input_rc", 1, {"timestamp": [9], "rc_lost": [1], "rc_failsafe": [1]}),
            D("vehicle_status", 0, {"timestamp": [1000000, 3000000, 2000000], "arming_state": [1, 2, 2],
                                    "nav_state": [2, 2, 5]}),
            D("input_rc", 0, {"timestamp": [500000], "rc_lost": [0], "rc_failsafe": [1]}),
            D("failsafe_flags", 0, {"timestamp": [0]}),
            D("sensor_gps", 0, {"timestamp": [0], "lat": [1]}),
        ], 10000000, {"COM_RC_IN_MODE": 1, "NAV_RCL_ACT": 2, "COM_RC_LOSS_T": 0.5, "OTHER": 3})
        topics, t_end, meta = c.extract(ul)
        self.assertEqual(sorted(topics), ["input_rc", "vehicle_status"])
        self.assertEqual(topics["vehicle_status"], {"ts": [1.0, 2.0, 3.0], "arming_state": [1, 2, 2],
                                                    "nav_state": [2, 5, 2]})
        self.assertEqual(topics["input_rc"], {"ts": [0.5], "rc_lost": [0], "rc_failsafe": [1]})
        self.assertEqual(t_end, 10.0)
        self.assertEqual(meta["instances"], {"input_rc": 2, "vehicle_status": 1, "failsafe_flags": 1})
        self.assertEqual(meta["missing_fields"], {"failsafe_flags": ["manual_control_signal_lost"]})
        self.assertEqual(meta["resorted"], ["vehicle_status"])
        self.assertEqual(meta["params"], {"COM_RC_IN_MODE": 1, "NAV_RCL_ACT": 2, "COM_RC_LOSS_T": 0.5,
                                          "COM_RCL_EXCEPT": None})

    def test_extract_then_analyse(self):
        ul = FakeULog([
            D("vehicle_status", 0, {"timestamp": [0, 10000000, 41000000, 100000000], "arming_state": [1, 2, 2, 1],
                                    "nav_state": [2, 2, 5, 5]}),
            D("vehicle_land_detected", 0, {"timestamp": [0, 20000000, 90000000], "landed": [1, 0, 1]}),
            D("failsafe_flags", 0, {"timestamp": [0, 15000000, 40000000, 41000000],
                                    "manual_control_signal_lost": [1, 0, 1, 0]}),
            D("manual_control_setpoint", 0, {"timestamp": [15000000, 39900000], "data_source": [2, 2]}),
        ], 120000000)
        topics, t_end, _meta = c.extract(ul)
        rec = c.core.analyse(topics, t_end)
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(rec["airborne_s"], 70)
        self.assertEqual(rec["events"], [{"onset": 40, "end": 41, "duration": 1, "cleared": True,
                                          "source": "mavlink", "rc_agrees": None, "nav_after": 5}])


def row(in_R=False, in_E=False, events=(), link="used", air=100.0, status="ok"):
    return {"log_id": "x", "mav": "Quadrotor", "download": "ok", "in_R": in_R, "in_E": in_E,
            "rec": {"status": status, "link": link, "airborne_s": air, "events": list(events),
                    "has": {"input_rc": True}, "sources_seen": ["rc"]}}


def ev(source, agrees):
    return {"onset": 1.0, "end": 2.0, "duration": 1.0, "cleared": True, "source": source, "rc_agrees": agrees,
            "nav_after": 5}


class TestGrade(unittest.TestCase):
    def test_counted(self):
        self.assertTrue(c.counted(row()))
        self.assertFalse(c.counted({"download": "missing"}))
        self.assertFalse(c.counted(dict(row(), duplicate_of="y")))
        self.assertFalse(c.counted({"download": "ok", "error": "x"}))

    def test_p1(self):
        rows = [row(in_R=True, events=[ev("mavlink", None)]) for _ in range(3)]
        rows += [row(in_R=True) for _ in range(147)]
        rows += [dict(row(in_R=True, events=[ev("rc", True)]), duplicate_of="z"), row(in_R=True, air=5.0),
                 row(in_R=True, link="never"), row(in_E=True, events=[ev("rc", True)])]
        g = c.grade(rows)
        self.assertEqual(g["P1_in_air_loss_share_R"]["result"], "held")
        self.assertEqual(g["P1_in_air_loss_share_R"]["n"], 150)
        self.assertEqual(g["P1_in_air_loss_share_R"]["k"], 3)
        self.assertEqual(g["P1_in_air_loss_share_R"]["share"], 0.02)
        self.assertEqual(g["P2_mavlink_share"]["result"], "too few")
        self.assertEqual(g["P2_mavlink_share"]["n"], 4)

    def test_p2_p4(self):
        rows = [row(in_E=True, events=[ev("mavlink", None)]) for _ in range(2)]
        rows += [row(in_E=True, events=[ev("rc", True)]) for _ in range(5)]
        rows += [row(in_E=True, events=[ev("rc", True), ev("mavlink", None)])]
        rows += [row(in_E=True, events=[ev("rc", False)]) for _ in range(2)]
        rows += [row(in_E=True, events=[ev("none", None)])]
        g = c.grade(rows)
        self.assertEqual(g["P1_in_air_loss_share_R"]["result"], "too few")
        self.assertEqual((g["P2_mavlink_share"]["result"], g["P2_mavlink_share"]["k"], g["P2_mavlink_share"]["n"]),
                         ("held", 2, 10))
        self.assertEqual(g["P3a_mavlink_no_input_rc_signal"]["result"], "too few")
        self.assertEqual(g["P4a_rc_input_rc_agrees"]["k"], 6)
        self.assertEqual(g["P4a_rc_input_rc_agrees"]["n"], 8)
        self.assertEqual(g["P4a_rc_input_rc_agrees"]["result"], "held")
        d = c.describe(rows)
        self.assertEqual(d["event_logs"], 11)
        self.assertEqual(d["events_total"], 12)
        self.assertEqual(d["first_event_source"], {"rc": 8, "mavlink": 2, "none": 1})
        cands = c.candidates(rows)
        self.assertEqual(len(cands), 11)
        self.assertEqual(cands[0]["first"]["nav_after"], "AUTO_RTL")

    def test_band_and_at_least(self):
        self.assertEqual(c.band(20, 150, 0.01, 0.10, 150)["result"], "failed")
        self.assertEqual(c.band(1, 150, 0.01, 0.10, 150)["result"], "failed")
        self.assertEqual(c.band(15, 150, 0.01, 0.10, 150)["result"], "held")
        self.assertEqual(c.band(2, 149, 0.01, 0.10, 150)["result"], "too few")
        self.assertEqual(c.at_least(4, 5, 0.80, 6)["result"], "too few")
        self.assertEqual(c.at_least(5, 6, 0.80, 6)["result"], "held")
        self.assertEqual(c.at_least(4, 6, 0.80, 6)["result"], "failed")


if __name__ == "__main__":
    unittest.main()

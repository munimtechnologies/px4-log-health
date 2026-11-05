"""Tests for checkup 0.3.4's u-blox dynamic model line (px4-gps-lag studies/dynmodel-2026).
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import json
import unittest
from unittest import mock

import checkup

QUIET = {"health_ok": True, "health_reason": "ok", "release": "v1.15.4", "flight_s": 420.0, "multicopter": True,
         "flags": [], "notes": []}
HEAD = "**u-blox dynamic model 6.**"


def dev(devtype, bus_type=5, bus=1, address=0):
    """A PX4 device id: bus type (low 3 bits), bus (next 5), address (byte 1), device type (byte 2)."""
    return (devtype << 16) | (address << 8) | (bus << 3) | bus_type


F9P_SERIAL = dev(0xAA)
M9_SERIAL = dev(0xA9, bus=2)


def inputs(model=6, ids=None, extra=None):
    params = {"EKF2_GPS_DELAY": 110.0}
    if model is not None:
        params["GPS_UBX_DYNMODEL"] = model
    params.update(extra or {})
    return {"params": params, "topic": "vehicle_gps_position", "stamps": [1, 2, 3], "samples": [1, 2, 3],
            "device_ids": [F9P_SERIAL] * 3 if ids is None else ids}


def lag(d=0.0, passed=True):
    return {"passed": passed, "reason": "ok" if passed else "too little manoeuvring", "lag_ms": 110.0 + d,
            "se_ms": 1.2, "param_gps_delay_ms": 110.0, "lag_minus_param_ms": d}


class Info(unittest.TestCase):
    def test_device_id_layout(self):
        self.assertEqual(F9P_SERIAL & 0x7, 5)
        self.assertEqual((F9P_SERIAL >> 16) & 0xFF, 0xAA)
        self.assertEqual(hex(F9P_SERIAL), "0xaa000d")

    def test_model_6_on_serial_ublox_gets_the_note(self):
        for ids in ([F9P_SERIAL] * 3, [M9_SERIAL] * 3):
            self.assertTrue(checkup.dynmodel_info(inputs(6, ids))["note"])
        for t in sorted(checkup.UBLOX_DEVTYPES):
            info = checkup.dynmodel_info(inputs(6, [dev(t)] * 4))
            self.assertEqual((info["value"], info["note"], info["devtype"]), (6, True, "0x%02X" % t))

    def test_other_models_get_no_note(self):
        for m in (0, 4, 7, 8, 2.0 + 5):
            info = checkup.dynmodel_info(inputs(m))
            self.assertEqual(info["note"], int(m) == 6, m)
        self.assertEqual(checkup.dynmodel_info(inputs(7))["value"], 7)

    def test_not_a_serial_ublox(self):
        self.assertIsNone(checkup.dynmodel_info(inputs(6, [dev(0xAA, bus_type=3)] * 3)))   # DroneCAN
        self.assertIsNone(checkup.dynmodel_info(inputs(6, [dev(0xAB)] * 3)))               # NMEA
        self.assertIsNone(checkup.dynmodel_info(inputs(6, [dev(0xA4)] * 3)))               # SBF
        self.assertIsNone(checkup.dynmodel_info(inputs(6, [11141433] * 3)))                # 0xAA on I2C

    def test_missing_pieces(self):
        self.assertIsNone(checkup.dynmodel_info(None))
        self.assertIsNone(checkup.dynmodel_info({}))
        self.assertIsNone(checkup.dynmodel_info(inputs(None)))
        self.assertIsNone(checkup.dynmodel_info(inputs(6, [])))
        self.assertIsNone(checkup.dynmodel_info(inputs(6, [0, 0])))
        self.assertIsNone(checkup.dynmodel_info(inputs(True)))
        self.assertIsNone(checkup.dynmodel_info(inputs("six")))

    def test_most_common_receiver_decides(self):
        mixed = [F9P_SERIAL] * 5 + [dev(0xAA, bus_type=3)] * 2
        self.assertTrue(checkup.dynmodel_info(inputs(6, mixed))["note"])
        mixed = [F9P_SERIAL] * 2 + [dev(0xAA, bus_type=3)] * 5
        self.assertIsNone(checkup.dynmodel_info(inputs(6, mixed)))

    def test_negative_int32_ids_are_read_as_unsigned(self):
        big = dev(0xAA) | (0x80 << 24)
        self.assertTrue(checkup.dynmodel_info(inputs(6, [big - (1 << 32)] * 3))["note"])


class Note(unittest.TestCase):
    def test_figures_match_the_studies(self):
        # dynmodel-2026 GRADE.md: K1 +45.6 ms, 244 graded logs, 41.5% of model-6 logs > 50 ms past setting.
        # GRADE-paired.md: 214.2 - 110.5 = 103.7 ms.
        self.assertEqual(round(45.6), 46)
        self.assertEqual(round(214.2 - 110.5, 1), 103.7)
        self.assertEqual(round(103.7), 104)
        for part in ("GPS_UBX_DYNMODEL = 6 (airborne <1g)", "every multicopter up to v1.16", "median 46 ms more",
                     "(244 vehicles)", "about 4 in 10", "PX4 PR #24831", "104 ms more",
                     "GPS_UBX_DYNMODEL = 7 (<2g", "since v1.17", "after a reboot",
                     "studies/dynmodel-2026/GRADE.md and GRADE-paired.md"):
            self.assertIn(part, checkup.DYNMODEL_NOTE)

    def report(self, model, lag_row, skipped=False, ids=None):
        gps = {"lag": None, "innov": None, "skipped": "gps_lag.py and innov.py not found"} if skipped \
            else {"lag": lag_row, "innov": None}
        with mock.patch.object(checkup, "run_health", return_value=dict(QUIET)), \
                mock.patch.object(checkup, "run_gps", return_value=gps), \
                mock.patch.object(checkup, "read_delay_inputs", return_value=inputs(model, ids)):
            return checkup.check("flight.ulg", None)

    def test_shown_once_for_model_6_whatever_the_lag(self):
        for row in (lag(0.0), lag(80.0), lag(-60.0), lag(0.0, passed=False)):
            r = self.report(6, row)
            self.assertTrue(r["gps_dynmodel"]["note"])
            md = checkup.render(r)
            self.assertEqual(md.count(HEAD), 1)
            self.assertGreater(md.index(HEAD), md.index("### GPS delay"))
            json.dumps(r)

    def test_after_the_mismatch_line(self):
        md = checkup.render(self.report(6, lag(80.0)))
        self.assertLess(md.index("studies/mismatch-2026/GRADE.md"), md.index(HEAD))

    def test_shown_when_gps_lag_is_missing(self):
        r = self.report(6, None, skipped=True)
        self.assertIsNone(r["gps_verdict"])
        md = checkup.render(r)
        self.assertIn("Not checked: gps_lag.py and innov.py not found.", md)
        self.assertIn(HEAD, md)

    def test_not_shown_otherwise(self):
        for model, ids in ((7, None), (8, None), (None, None), (6, [dev(0xAA, bus_type=3)] * 3)):
            md = checkup.render(self.report(model, lag(0.0), ids=ids))
            self.assertNotIn(HEAD, md, (model, ids))

    def test_not_a_flag(self):
        r = self.report(6, lag(0.0))
        self.assertFalse(checkup.flagged(r))

    def test_unreadable_log_has_no_dynmodel(self):
        with mock.patch.object(checkup, "run_health", return_value=dict(QUIET)), \
                mock.patch.object(checkup, "run_gps", return_value={"lag": lag(0.0), "innov": None}), \
                mock.patch.object(checkup, "read_delay_inputs", side_effect=OSError("cannot open")):
            r = checkup.check("flight.ulg", None)
        self.assertIsNone(r["gps_dynmodel"])
        self.assertNotIn(HEAD, checkup.render(r))


if __name__ == "__main__":
    unittest.main()

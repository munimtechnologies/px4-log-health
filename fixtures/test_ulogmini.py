"""Synthetic tests for ulogmini: every log here is built byte by byte in the test (no real logs, no network).
Apache-2.0. Made with AI agents working in the name of MV2, Munim, Inc."""
import os
import struct
import sys
import tempfile
import types
import unittest

import ulogmini

S = 1_000_000
STATUS_FMT = "vehicle_status:uint64_t timestamp;uint8_t arming_state;uint8_t nav_state;uint8_t[6] _padding0;"
RC_FMT = "input_rc:uint64_t timestamp;bool rc_failsafe;bool rc_lost;uint8_t[6] _padding0;"
LAND_FMT = "vehicle_land_detected:uint64_t timestamp;bool landed;uint8_t[7] _padding0;"


def m(t, body):
    return struct.pack("<HB", len(body), ord(t)) + body


def head(ts=500_000):
    return b"ULog\x01\x12\x35\x01" + struct.pack("<Q", ts)


def flags():
    return m("B", bytes(40))


def fmt(s):
    return m("F", s.encode())


def kv(t, key, raw):
    k = key.encode()
    return m(t, bytes([len(k)]) + k + raw)


def add(msg_id, name, multi=0):
    return m("A", bytes([multi]) + struct.pack("<H", msg_id) + name.encode())


def data(msg_id, payload):
    return m("D", struct.pack("<H", msg_id) + payload)


def logmsg(ts, text, level=b"6"):
    return m("L", level + struct.pack("<Q", ts) + text.encode())


def status(ts, arm, nav):
    return struct.pack("<QBB", ts, arm, nav) + bytes(6)


def rc(ts, failsafe, lost):
    return struct.pack("<QBB", ts, failsafe, lost) + bytes(6)


def land(ts, landed):
    return struct.pack("<QB", ts, landed) + bytes(7)


def defs():
    return (head() + flags() + fmt(STATUS_FMT) + fmt(RC_FMT) + fmt(LAND_FMT)
            + kv("I", "char[4] sys_name", b"PX4\x00") + kv("I", "uint32_t ver_sw_release", struct.pack("<I", 0x010E00FF))
            + kv("P", "int32_t MAV_TYPE", struct.pack("<i", 2)) + kv("P", "float BAT1_V_EMPTY", struct.pack("<f", 3.5)))


class TempLog:
    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        fd, self.path = tempfile.mkstemp(suffix=".ulg")
        with os.fdopen(fd, "wb") as f:
            f.write(self.raw)
        return self.path

    def __exit__(self, *exc):
        os.unlink(self.path)


def read(raw, topics=None):
    with TempLog(raw) as p:
        return ulogmini.ULog(p, topics, True)


def topic(u, name, multi=0):
    for d in u.data_list:
        if d.name == name and d.multi_id == multi:
            return d.data
    return None


class Helpers(unittest.TestCase):
    def test_split_type(self):
        self.assertEqual(ulogmini.split_type("float[4]"), ("float", 4))
        self.assertEqual(ulogmini.split_type("uint64_t"), ("uint64_t", None))
        with self.assertRaises(ValueError):
            ulogmini.split_type("float[x]")

    def test_parse_format(self):
        name, fields = ulogmini.parse_format(STATUS_FMT)
        self.assertEqual(name, "vehicle_status")
        self.assertEqual(fields[0], ("uint64_t", "timestamp", None))
        self.assertEqual(fields[3], ("uint8_t", "_padding0", 6))
        with self.assertRaises(ValueError):
            ulogmini.parse_format("no colon here")

    def test_layout_padding_and_size(self):
        formats = dict([ulogmini.parse_format(STATUS_FMT)])
        st, names, tail = ulogmini.compile_layout(ulogmini.layout(formats, "vehicle_status"))
        self.assertEqual((st.size, names, tail), (16, ["timestamp", "arming_state", "nav_state"], 6))

    def test_layout_arrays_and_nesting(self):
        formats = dict([ulogmini.parse_format("bar:float x;float y;"),
                        ulogmini.parse_format("foo:uint64_t timestamp;bar[2] b;uint16_t[2] c;")])
        st, names, tail = ulogmini.compile_layout(ulogmini.layout(formats, "foo"))
        self.assertEqual(names, ["timestamp", "b[0].x", "b[0].y", "b[1].x", "b[1].y", "c[0]", "c[1]"])
        self.assertEqual((st.size, tail), (8 + 16 + 4, 0))

    def test_unknown_type(self):
        formats = dict([ulogmini.parse_format("foo:uint64_t timestamp;mystery q;")])
        with self.assertRaises(ValueError):
            ulogmini.layout(formats, "foo")

    def test_decode_value(self):
        self.assertEqual(ulogmini.decode_value("char[4]", b"PX4\x00"), "PX4")
        self.assertEqual(ulogmini.decode_value("int32_t", struct.pack("<i", -7)), -7)
        self.assertEqual(ulogmini.decode_value("float[2]", struct.pack("<2f", 1.5, 2.0)), [1.5, 2.0])
        self.assertIsNone(ulogmini.decode_value("int32_t", b"\x01"))


class Reader(unittest.TestCase):
    def test_basic(self):
        raw = (defs() + add(0, "vehicle_status") + data(0, status(1 * S, 1, 2)) + data(0, status(2 * S, 2, 3))
               + logmsg(int(2.5 * S), "Armed by RC"))
        u = read(raw)
        self.assertEqual(u.file_version, 1)
        self.assertEqual(u.start_timestamp, 500_000)
        self.assertEqual(u.last_timestamp, 2 * S)
        self.assertEqual(u.msg_info_dict["sys_name"], "PX4")
        self.assertEqual(u.msg_info_dict["ver_sw_release"], 0x010E00FF)
        self.assertEqual(u.initial_parameters, {"MAV_TYPE": 2, "BAT1_V_EMPTY": 3.5})
        d = topic(u, "vehicle_status")
        self.assertEqual(d, {"timestamp": [1 * S, 2 * S], "arming_state": [1, 2], "nav_state": [2, 3]})
        self.assertEqual([(x.timestamp, x.log_level, x.message) for x in u.logged_messages],
                         [(int(2.5 * S), ord("6"), "Armed by RC")])
        self.assertEqual((u.corruptions, u.truncated, u.short_rows), (0, False, 0))

    def test_float_array_topic(self):
        raw = (head() + flags() + fmt("actuator_outputs:uint64_t timestamp;uint32_t noutputs;float[3] output;")
               + add(4, "actuator_outputs", multi=1)
               + data(4, struct.pack("<QI3f", 7 * S, 3, 1500.0, 0.0, 0.1)))
        u = read(raw)
        d = topic(u, "actuator_outputs", multi=1)
        self.assertEqual(d["noutputs"], [3])
        self.assertEqual((d["output[0]"], d["output[1]"]), ([1500.0], [0.0]))
        self.assertEqual(d["output[2]"], [struct.unpack("<f", struct.pack("<f", 0.1))[0]])

    def test_filter_and_empty_dropped(self):
        raw = (defs() + add(0, "vehicle_status") + add(1, "input_rc") + add(2, "vehicle_land_detected")
               + data(0, status(1 * S, 2, 2)) + data(1, rc(2 * S, 0, 0)))
        u = read(raw, ["vehicle_status", "vehicle_land_detected"])
        self.assertEqual(sorted(d.name for d in u.data_list), ["vehicle_status"])
        self.assertEqual(u.last_timestamp, 1 * S)

    def test_trailing_padding_may_be_omitted(self):
        raw = defs() + add(0, "vehicle_status") + data(0, struct.pack("<QBB", 3 * S, 2, 4))
        self.assertEqual(topic(read(raw), "vehicle_status")["nav_state"], [4])

    def test_too_short_row_skipped(self):
        raw = defs() + add(0, "vehicle_status") + data(0, struct.pack("<QB", 3 * S, 2))
        u = read(raw)
        self.assertIsNone(topic(u, "vehicle_status"))
        self.assertEqual(u.short_rows, 1)

    def test_corruption_then_sync(self):
        raw = (defs() + add(0, "vehicle_status") + data(0, status(1 * S, 2, 2))
               + b"\x07\x00\x99\x01\x02" + m("S", ulogmini.SYNC) + data(0, status(2 * S, 2, 3)))
        u = read(raw)
        self.assertEqual(topic(u, "vehicle_status")["timestamp"], [1 * S, 2 * S])
        self.assertEqual(u.corruptions, 1)

    def test_truncated_end(self):
        raw = defs() + add(0, "vehicle_status") + data(0, status(1 * S, 2, 2)) + struct.pack("<HB", 20, ord("D")) + b"\x00" * 5
        u = read(raw)
        self.assertTrue(u.truncated)
        self.assertEqual(topic(u, "vehicle_status")["timestamp"], [1 * S])

    def test_changed_parameter_and_remove(self):
        raw = (defs() + add(0, "vehicle_status") + data(0, status(1 * S, 2, 2))
               + kv("P", "int32_t COM_RC_LOSS_T", struct.pack("<i", 3))
               + m("R", bytes([0]) + struct.pack("<H", 0)) + data(0, status(2 * S, 2, 2)))
        u = read(raw)
        self.assertEqual(u.changed_parameters, [(1 * S, "COM_RC_LOSS_T", 3)])
        self.assertNotIn("COM_RC_LOSS_T", u.initial_parameters)
        self.assertEqual(topic(u, "vehicle_status")["timestamp"], [1 * S])

    def test_not_a_ulog(self):
        with self.assertRaises(ValueError):
            read(b"PK\x03\x04" + bytes(40))


class WithFixscreen(unittest.TestCase):
    """The stand-in must give fixscreen.scan() the same view pyulog would."""

    def setUp(self):
        self.saved = sys.modules.get("pyulog")
        shim = types.ModuleType("pyulog")
        shim.ULog = ulogmini.ULog
        shim.__version__ = "ulogmini-" + ulogmini.__version__
        sys.modules["pyulog"] = shim

    def tearDown(self):
        if self.saved is None:
            sys.modules.pop("pyulog", None)
        else:
            sys.modules["pyulog"] = self.saved

    def test_scan_rc_loss(self):
        import fixscreen
        raw = (defs() + add(0, "vehicle_status") + add(1, "input_rc") + add(2, "vehicle_land_detected")
               + data(0, status(1 * S, 2, 2)) + data(2, land(1 * S, 0))
               + data(1, rc(2 * S, 0, 0)) + data(1, rc(3 * S, 0, 1))
               + logmsg(int(3.1 * S), "Manual control lost") + data(1, rc(5 * S, 0, 0)))
        with TempLog(raw) as p:
            rec = fixscreen.scan(p)
        self.assertEqual(rec["counts"], {"rc_loss": 1})
        self.assertEqual((rec["release"], rec["sys_name"], rec["params"]), ("v1.14.0", "PX4", {"MAV_TYPE": 2, "BAT1_V_EMPTY": 3.5}))
        self.assertEqual(rec["duration_s"], 4.5)
        self.assertEqual(rec["topics"], ["input_rc", "vehicle_land_detected", "vehicle_status"])
        w = rec["found"]["rc_loss"][0]
        self.assertEqual((w["start"], w["end"], w["dur_us"], w["closed_by"]), (3 * S, 5 * S, 2 * S, "recovery"))
        self.assertEqual((w["airborne"], w["mode"], w["fr_time"]), (True, "POSCTL", "0:02.50"))
        self.assertEqual(w["messages"], [["0:02.60", ord("6"), "Manual control lost"]])


if __name__ == "__main__":
    unittest.main()

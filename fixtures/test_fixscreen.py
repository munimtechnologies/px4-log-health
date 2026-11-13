"""Synthetic tests for fixscreen (no logs, no network). Apache-2.0. Made with AI agents working in the name of
MV2, Munim, Inc."""
import unittest

import fixscreen as fs

S = 1_000_000


def cols(rows):
    """[{field: value}] -> {field: [values]} (every row must carry the same fields)."""
    out = {}
    for r in rows:
        for k, v in r.items():
            out.setdefault(k, []).append(v)
    return out


def armed_at(t, state=2, nav=2):
    return cols([{"timestamp": t, "arming_state": state, "nav_state": nav}])


class Release(unittest.TestCase):
    def test_decode(self):
        self.assertEqual(fs.decode_release(0x010E00FF), "v1.14.0")
        self.assertEqual(fs.decode_release(0x010B0080), "v1.11.0-beta")
        self.assertIsNone(fs.decode_release(None))
        self.assertIsNone(fs.decode_release(True))


class Rc(unittest.TestCase):
    def rc(self, seq):
        return cols([{"timestamp": t, "rc_lost": v, "rc_failsafe": 0} for t, v in seq])

    def test_window(self):
        w = fs.rc_oracle(armed_at(1 * S), self.rc([(2 * S, 0), (3 * S, 1), (5 * S, 0)]))
        self.assertEqual(len(w), 1)
        self.assertEqual((w[0]["start"], w[0]["end"], w[0]["dur_us"]), (3 * S, 5 * S, 2 * S))
        self.assertEqual((w[0]["closed_by"], w[0]["severity"]), ("recovery", "warning"))

    def test_critical(self):
        w = fs.rc_oracle(armed_at(1 * S), self.rc([(2 * S, 1), (8 * S, 0)]))
        self.assertEqual(w[0]["severity"], "critical")

    def test_short_ignored(self):
        self.assertEqual(fs.rc_oracle(armed_at(1 * S), self.rc([(2 * S, 1), (2 * S + 200_000, 0)])), [])

    def test_disarmed(self):
        self.assertEqual(fs.rc_oracle(armed_at(1 * S, state=1), self.rc([(2 * S, 1), (10 * S, 0)])), [])

    def test_failsafe_flag_counts(self):
        rc = cols([{"timestamp": 2 * S, "rc_lost": 0, "rc_failsafe": 1},
                   {"timestamp": 4 * S, "rc_lost": 0, "rc_failsafe": 0}])
        self.assertEqual(fs.rc_oracle(armed_at(1 * S), rc)[0]["dur_us"], 2 * S)

    def test_end_of_log_and_disarm(self):
        w = fs.rc_oracle(armed_at(1 * S), self.rc([(3 * S, 1), (4 * S, 1)]))
        self.assertEqual((w[0]["end"], w[0]["closed_by"]), (4 * S, "end of log"))
        st = cols([{"timestamp": 1 * S, "arming_state": 2, "nav_state": 2},
                   {"timestamp": 6 * S, "arming_state": 1, "nav_state": 2}])
        w = fs.rc_oracle(st, self.rc([(3 * S, 1), (5 * S, 1), (9 * S, 0)]))
        self.assertEqual((w[0]["start"], w[0]["end"], w[0]["closed_by"]), (3 * S, 6 * S, "disarm"))

    def test_missing_fields(self):
        self.assertEqual(fs.rc_oracle(armed_at(1 * S), cols([{"timestamp": 2 * S}])), [])


class Battery(unittest.TestCase):
    def b(self, seq, connected=1, cells=4):
        return cols([{"timestamp": t, "connected": connected, "cell_count": cells, "voltage_v": v,
                      "voltage_filtered_v": v, "current_a": 15.0} for t, v in seq])

    def test_detect_and_run(self):
        # 4S threshold 13.2 V; 12.5 V at 40, 41, 42 s, recovered at 43 s
        d = fs.battery_oracle(armed_at(int(0.5 * S)), self.b([(1 * S, 16.0), (40 * S, 12.5), (41 * S, 12.2),
                                                              (42 * S, 12.9), (43 * S, 14.0)]))
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]["t"], d[0]["threshold_v"], d[0]["current_a"]), (40 * S, 13.2, 15.0))
        self.assertEqual((d[0]["below_us"], d[0]["below_end"], d[0]["min_v"]), (3 * S, "recovered", 12.2))
        self.assertEqual(d[0]["v_per_cell"], 3.125)

    def test_dedup_30s(self):
        d = fs.battery_oracle(armed_at(1), self.b([(40 * S, 12.0), (50 * S, 12.0), (71 * S, 12.0)]))
        self.assertEqual([x["t"] for x in d], [40 * S, 71 * S])
        self.assertEqual(d[0]["below_end"], "next detection")

    def test_disconnected_and_disarmed(self):
        self.assertEqual(fs.battery_oracle(armed_at(1), self.b([(40 * S, 10.0)], connected=0)), [])
        self.assertEqual(fs.battery_oracle(armed_at(1, state=1), self.b([(40 * S, 10.0)])), [])
        self.assertEqual(fs.battery_oracle(armed_at(1), self.b([(40 * S, 10.0)], cells=0)), [])

    def test_filtered_fallback(self):
        b = cols([{"timestamp": 40 * S, "connected": 1, "cell_count": 6, "voltage_v": 0.0,
                   "voltage_filtered_v": 19.0, "current_a": float("nan")}])
        d = fs.battery_oracle(armed_at(1), b)
        self.assertEqual((d[0]["voltage_v"], d[0]["threshold_v"], d[0]["current_a"]), (19.0, 19.8, None))

    def test_strictly_below(self):
        self.assertEqual(fs.battery_oracle(armed_at(1), self.b([(40 * S, 13.2)])), [])


class Gps(unittest.TestCase):
    def g(self, extra):
        rows = [{"timestamp": k * S, "eph": 1.0, "epv": 2.0, "satellites_used": 12, "fix_type": 3}
                for k in range(1, 11)]
        rows += [{"timestamp": t * S, "eph": e, "epv": v, "satellites_used": s, "fix_type": 3}
                 for t, e, v, s in extra]
        return cols(rows)

    def test_baseline_not_checked(self):
        # the 10th baseline sample itself is never checked
        rows = [{"timestamp": k * S, "eph": 1.0, "epv": 2.0, "satellites_used": 12, "fix_type": 3} for k in range(1, 10)]
        rows.append({"timestamp": 10 * S, "eph": 9.0, "epv": 2.0, "satellites_used": 12, "fix_type": 3})
        self.assertEqual(fs.gps_oracle(cols(rows)), [])

    def test_spike_drop_dedup(self):
        d = fs.gps_oracle(self.g([(11, 8.0, 2.0, 12), (20, 1.0, 2.0, 3), (22, 8.0, 2.0, 12), (30, 1.0, 11.0, 12)]))
        self.assertEqual([(x["t"], x["reasons"], x["severity"]) for x in d],
                         [(11 * S, ["eph_spike"], "warning"), (20 * S, ["satellite_drop"], "critical"),
                          (30 * S, ["epv"], "warning")])
        self.assertEqual((d[0]["baseline_eph"], d[0]["baseline_sats"]), (1.0, 12.0))

    def test_eph_needs_3x(self):
        rows = [{"timestamp": k * S, "eph": 2.0, "epv": 2.0, "satellites_used": 12, "fix_type": 3} for k in range(1, 11)]
        rows.append({"timestamp": 11 * S, "eph": 5.5, "epv": 2.0, "satellites_used": 12, "fix_type": 3})
        self.assertEqual(fs.gps_oracle(cols(rows)), [])

    def test_bad_sample_resets_baseline(self):
        rows = [{"timestamp": k * S, "eph": 1.0, "epv": 2.0, "satellites_used": 12, "fix_type": 3} for k in range(1, 6)]
        rows.append({"timestamp": 6 * S, "eph": 1.0, "epv": 2.0, "satellites_used": 12, "fix_type": 2})
        rows += [{"timestamp": k * S, "eph": 1.0, "epv": 2.0, "satellites_used": 12, "fix_type": 3} for k in range(7, 16)]
        rows.append({"timestamp": 16 * S, "eph": 8.0, "epv": 2.0, "satellites_used": 12, "fix_type": 3})
        # only 9 good samples after the reset (7..15), so 16 s completes the baseline and is not checked
        self.assertEqual(fs.gps_oracle(cols(rows)), [])


class Motor(unittest.TestCase):
    def o(self, seq):
        return cols([{"timestamp": t, "noutputs": 2, "output[0]": a, "output[1]": b} for t, a, b in seq])

    def test_drop(self):
        outs = self.o([(1 * S, 1500.0, 1500.0), (2 * S, 1500.0, 0.0), (3 * S, 1500.0, 0.0), (4 * S, 1500.0, 1200.0)])
        d = fs.motor_oracle(armed_at(0, nav=3), outs)
        self.assertEqual([(x["t"], x["channel"], x["previous"], x["nav_state"]) for x in d], [(2 * S, 1, 1500.0, 3)])
        self.assertEqual(fs.zero_run_us(outs, 1, 2 * S), 2 * S)

    def test_disarmed_and_unused(self):
        self.assertEqual(fs.motor_oracle(armed_at(0, state=1), self.o([(1 * S, 1500.0, 1500.0), (2 * S, 0.0, 0.0)])), [])
        outs = cols([{"timestamp": 1 * S, "noutputs": 1, "output[0]": 1500.0, "output[1]": 1500.0},
                     {"timestamp": 2 * S, "noutputs": 1, "output[0]": 1500.0, "output[1]": 0.0}])
        self.assertEqual(fs.motor_oracle(armed_at(0), outs), [])

    def test_disarm_clears(self):
        st = cols([{"timestamp": 0, "arming_state": 2, "nav_state": 2},
                   {"timestamp": int(1.5 * S), "arming_state": 1, "nav_state": 2},
                   {"timestamp": int(1.7 * S), "arming_state": 2, "nav_state": 2}])
        self.assertEqual(fs.motor_oracle(st, self.o([(1 * S, 1500.0, 1500.0), (2 * S, 0.0, 0.0)])), [])


class Ranking(unittest.TestCase):
    def test_airborne_first(self):
        a = fs.score("rc_loss", {"airborne": False, "dur_us": 5 * S}, 100)
        b = fs.score("rc_loss", {"airborne": True, "dur_us": 600_000}, 10 ** 8)
        self.assertLess(b, a)

    def test_fr_time(self):
        self.assertEqual(fs.fr_time(75.5), "1:15.50")


if __name__ == "__main__":
    unittest.main()

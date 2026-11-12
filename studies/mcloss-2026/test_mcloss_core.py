"""Synthetic tests for mcloss_core.py (no logs, no network). By MV2 of Munim, Inc.; written with AI agents.
Apache-2.0."""

import unittest

import mcloss_core as m

F, T = False, True


class TestIntervals(unittest.TestCase):
    def test_intervals_where(self):
        self.assertEqual(m.intervals_where([0, 1, 2, 3, 4], [0, 2, 2, 1, 2], lambda v: v == 2, 5),
                         [(1, 3), (4, 5)])

    def test_intervals_cut_at_end(self):
        self.assertEqual(m.intervals_where([0, 1, 2, 3, 4], [0, 2, 2, 1, 2], lambda v: v == 2, 3.5), [(1, 3)])

    def test_intersect(self):
        self.assertEqual(m.intersect([(0, 10)], [(2, 3), (5, 12)]), [(2, 3), (5, 10)])

    def test_airborne(self):
        armed, air = m.airborne_intervals([0, 10, 100], [1, 2, 1], [0, 20, 90], [T, F, T], 120)
        self.assertEqual(armed, [(10, 100)])
        self.assertEqual(air, [(20, 90)])

    def test_unsorted_raises(self):
        with self.assertRaises(ValueError):
            m.intervals_where([0, 2, 1], [1, 1, 1], bool, 5)

    def test_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            m.value_at([0, 1], [1], 0.5)


class TestEvents(unittest.TestCase):
    AIR = [(20, 90)]

    def test_events(self):
        ts = [0, 30, 30.4, 40, 41, 60, 95, 96]
        lost = [F, T, F, T, F, T, F, T]
        ev = m.loss_events(ts, lost, self.AIR, 120)
        self.assertEqual(ev, [{"onset": 40, "end": 41, "duration": 1, "cleared": True},
                              {"onset": 60, "end": 95, "duration": 35, "cleared": True}])

    def test_open_at_end(self):
        ev = m.loss_events([0, 50], [F, T], self.AIR, 120)
        self.assertEqual(ev, [{"onset": 50, "end": 120, "duration": 70, "cleared": False}])

    def test_true_from_first_sample_is_not_event(self):
        self.assertEqual(m.loss_events([0, 50], [T, T], self.AIR, 120), [])

    def test_link_status(self):
        armed = [(10, 100)]
        self.assertEqual(m.link_status([0, 50], [T, T], armed, 120), "never")
        self.assertEqual(m.link_status([0, 50], [T, F], armed, 120), "used")
        self.assertEqual(m.link_status([], [], armed, 120), "no_flag")
        self.assertEqual(m.link_status([0, 50], [T, F], [], 120), "not_armed")


class TestLookups(unittest.TestCase):
    def test_value_at(self):
        ts, vals = [1, 2, 3], ["a", "b", "c"]
        self.assertIsNone(m.value_at(ts, vals, 0.5))
        self.assertEqual(m.value_at(ts, vals, 2), "b")
        self.assertEqual(m.value_at(ts, vals, 2.5), "b")
        self.assertEqual(m.value_at(ts, vals, 10), "c")
        self.assertIsNone(m.value_at(ts, vals, 10, max_age=5))

    def test_source_before(self):
        ts, src = [10, 20, 39.8, 40, 40.2], [1, 1, 3, 0, 0]
        self.assertEqual(m.source_before(ts, src, 40), "mavlink")
        self.assertEqual(m.source_before(ts, src, 39.8), "none")
        self.assertEqual(m.source_before(ts, src, 12), "rc")
        self.assertEqual(m.source_before(ts, src, 5), "none")

    def test_source_class(self):
        self.assertEqual([m.source_class(c) for c in (None, 0, 1, 2, 7, 9)],
                         ["none", "unknown", "rc", "mavlink", "mavlink", "unknown"])

    def test_rc_agrees(self):
        ts = [0, 38, 38.5, 39.5, 42.5]
        self.assertIsNone(m.rc_agrees([], [], [], 40, 41))
        self.assertTrue(m.rc_agrees(ts, [F, F, F, T, F], [F] * 5, 40, 41))
        self.assertTrue(m.rc_agrees(ts, [F] * 5, [F, F, T, F, F], 40, 41))
        self.assertFalse(m.rc_agrees(ts, [F] * 5, [F] * 5, 40, 41))
        self.assertFalse(m.rc_agrees(ts, [F, F, F, F, T], [F] * 5, 40, 41))

    def test_nav_after(self):
        self.assertEqual(m.nav_after([0, 41], [2, 5], 40), 5)
        self.assertEqual(m.nav_after([0, 41], [2, 5], 30), 2)


class TestAnalyse(unittest.TestCase):
    def topics(self):
        return {
            "vehicle_status": {"ts": [0, 10, 41, 100], "arming_state": [1, 2, 2, 1], "nav_state": [2, 2, 5, 5]},
            "vehicle_land_detected": {"ts": [0, 20, 90], "landed": [1, 0, 1]},
            "failsafe_flags": {"ts": [0, 15, 40, 41], "manual_control_signal_lost": [1, 0, 1, 0]},
            "manual_control_setpoint": {"ts": [15, 39.9], "data_source": [2, 2]},
        }

    def test_analyse(self):
        rec = m.analyse(self.topics(), 120)
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(rec["armed_s"], 90)
        self.assertEqual(rec["airborne_s"], 70)
        self.assertEqual(rec["link"], "used")
        self.assertEqual(rec["sources_seen"], ["mavlink"])
        self.assertFalse(rec["has"]["input_rc"])
        self.assertEqual(rec["events"], [{"onset": 40, "end": 41, "duration": 1, "cleared": True,
                                          "source": "mavlink", "rc_agrees": None, "nav_after": 5}])

    def test_missing(self):
        t = self.topics()
        del t["failsafe_flags"]
        rec = m.analyse(t, 120)
        self.assertEqual(rec["status"], "missing_topics")
        self.assertEqual(rec["events"], [])


if __name__ == "__main__":
    unittest.main()

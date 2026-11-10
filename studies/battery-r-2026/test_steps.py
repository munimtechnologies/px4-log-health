"""Synthetic tests for steps.py (no flight log, no network). They run before the pass reads any
log. By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import json
import os
import tempfile
import unittest

import steps as st


def m(collapsed, r, first_zero_s=None, valid=False):
    out = {"read": "ok", "measurable": True, "collapsed": collapsed, "step_valid": valid,
           "first_zero_s": first_zero_s}
    out["step"] = None if r is False else {"pairs": 500, "range_a": 10.0, "r": r, "mohm_per_cell": 1.0}
    return out


def take(r):
    return m(True, r, 0.0)


def alive(r, valid=False):
    return m(False, r, None, valid)


def late(r, valid):
    return m(True, r, 5.0, valid)


class Helpers(unittest.TestCase):
    def test_flat(self):
        self.assertTrue(st.is_flat(alive(0.3)))
        self.assertTrue(st.is_flat(alive(-0.05)))
        self.assertFalse(st.is_flat(alive(-0.1)))            # the line itself is not flat
        self.assertFalse(st.is_flat(alive(-0.8)))
        self.assertFalse(st.is_flat(alive(None)))
        self.assertFalse(st.is_flat(alive(False)))           # no step fit at all

    def test_timing(self):
        self.assertTrue(st.at_takeoff(take(0.1)))
        self.assertTrue(st.at_takeoff(m(True, 0.1, 0)))
        self.assertFalse(st.at_takeoff(late(0.1, False)))
        self.assertFalse(st.at_takeoff(m(False, 0.1, 0.0)))  # not collapsed
        self.assertTrue(st.later(late(0.1, False)))
        self.assertFalse(st.later(take(0.1)))
        self.assertFalse(st.later(m(True, 0.1, None)))

    def test_measures(self):
        recs = [{"measure": take(0.1)}, {"measure": {"read": "ok", "measurable": False}},
                {"measure": {"read": "no battery_status data"}}, {"status": "too big"}, {"measure": alive(-0.8)}]
        self.assertEqual(len(st.measures(recs)), 2)


class Grade(unittest.TestCase):
    def test_all_hold(self):
        ms = [take(r) for r in (0.2, 0.1, -0.05, 0.0, -0.5)]
        ms += [alive(0.1) for _ in range(6)] + [alive(-0.8, True) for _ in range(16)]
        ms += [late(-0.6, True), late(-0.6, True), late(-0.6, False)]
        p, rep = st.grade(ms)
        self.assertEqual(p, {"S1": True, "S2": True, "S3": True, "S4": True})
        self.assertEqual(rep["measurable"], 30)
        self.assertEqual(rep["collapsed"], 8)
        self.assertEqual((rep["at_takeoff"], rep["at_takeoff_with_r"], rep["at_takeoff_flat"]), (5, 5, 4))
        self.assertEqual(rep["at_takeoff_flat_share"], 0.8)
        self.assertEqual(rep["at_takeoff_r"], [-0.5, -0.05, 0.0, 0.1, 0.2])
        self.assertEqual((rep["alive_with_r"], rep["alive_flat"], rep["alive_flat_share"]), (22, 6, 0.2727))
        self.assertEqual(rep["alive_valid"], 16)
        self.assertEqual((rep["flat"], rep["flat_collapsed"], rep["flat_collapsed_share"]), (10, 4, 0.4))
        self.assertEqual((rep["steep"], rep["steep_collapsed"], rep["steep_collapsed_share"]), (20, 4, 0.2))
        self.assertEqual((rep["later"], rep["later_valid"], rep["later_valid_share"]), (3, 2, 0.6667))
        self.assertEqual(rep["later_first_zero_s"], [5.0, 5.0, 5.0])
        json.dumps(rep)

    def test_too_few_is_not_graded(self):
        ms = [take(0.2) for _ in range(4)] + [alive(-0.8, True) for _ in range(19)]
        ms += [late(-0.6, True), late(-0.6, True)]
        p, _ = st.grade(ms)
        self.assertEqual(p, {"S1": None, "S2": None, "S3": None, "S4": None})

    def test_failures_and_prefix(self):
        ms = [take(-0.5) for _ in range(5)]                     # S1: 0 of 5 flat
        ms += [alive(0.2) for _ in range(10)] + [alive(-0.8) for _ in range(10)]   # S2: 10 of 20 flat
        ms += [late(-0.2, False) for _ in range(3)]             # S4: 0 of 3 valid
        p, rep = st.grade(ms, "H")
        self.assertEqual(p["H1"], False)
        self.assertEqual(p["H2"], False)
        self.assertEqual(p["H3"], False)                         # flat 10, collapsed among them 0
        self.assertEqual(p["H4"], False)
        self.assertEqual(rep["flat"], 10)

    def test_thresholds_are_inclusive(self):
        ms = [take(0.1) for _ in range(3)] + [take(-0.5) for _ in range(2)]        # 3 / 5 = 60 %
        ms += [alive(0.1) for _ in range(6)] + [alive(-0.8) for _ in range(14)]    # 6 / 20 = 30 %
        p, rep = st.grade(ms)
        self.assertTrue(p["S1"])
        self.assertTrue(p["S2"])
        self.assertEqual((rep["flat"], rep["flat_collapsed"]), (9, 3))
        self.assertIsNone(p["S3"])                                                 # 9 flat < 10

    def test_no_step_fit_is_left_out(self):
        ms = [take(False) for _ in range(5)] + [take(0.2) for _ in range(5)]
        p, rep = st.grade(ms)
        self.assertEqual((rep["at_takeoff"], rep["at_takeoff_with_r"]), (10, 5))
        self.assertTrue(p["S1"])
        self.assertEqual(rep["with_r"], 5)


class Files(unittest.TestCase):
    def test_skip_vehicles(self):
        d = tempfile.mkdtemp()
        a, b = os.path.join(d, "a.jsonl"), os.path.join(d, "b.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"vehicle": "v1"}) + "\n\n" + json.dumps({"vehicle": "v2"}) + "\n")
        with open(b, "w", encoding="utf-8") as f:
            f.write(json.dumps({"vehicle": "v2"}) + "\n" + json.dumps({"vehicle": "v3"}) + "\n"
                    + json.dumps({"order": 9}) + "\n")
        self.assertEqual(st.skip_vehicles(a, b), {"v1", "v2", "v3"})

    def test_collapsed_rows(self):
        recs = [{"order": 1, "log_id": "x", "params": {"BAT1_R_INTERNAL": -1.0}, "measure": take(0.3)},
                {"order": 2, "log_id": "y", "measure": alive(-0.8)}]
        rows = st.collapsed_rows(recs)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["order"], rows[0]["r_internal"], rows[0]["first_zero_s"]), (1, -1.0, 0.0))
        self.assertEqual(rows[0]["step"]["r"], 0.3)


if __name__ == "__main__":
    unittest.main()

"""Synthetic tests for trace_timing.py. Apache-2.0. Made with AI agents working in MV2's name."""
import json
import os
import tempfile
import unittest

import trace_timing as tt


def synth(lag=2, n=601, step_k=300, onset_k=303):
    """100 Hz; current 2 A -> 8 A at sample step_k; voltage follows `lag` samples late; R to 0 at onset_k."""
    t = [1.0 + k * 0.01 for k in range(n)]
    cur = [2.0 if k < step_k else 8.0 for k in range(n)]
    v = [16.4 - 0.08 * cur[max(0, k - lag)] for k in range(n)]
    r = [0.005 if k < onset_k else 0.0 for k in range(n)]
    return {"t": t, "v": v, "i": cur, "r": r, "landed": [], "armed": [], "motors": [], "motor_names": [],
            "info": {}}


class TestColumns(unittest.TestCase):
    def test_px4_names(self):
        header = ["timestamp", "voltage_v", "voltage_filtered_v", "current_a", "current_filtered_a",
                  "internal_resistance_estimate"]
        rows = [[1000000 + 10000 * k, 16.0, 16.0, 2.0, 2.0, 0.004] for k in range(30)]
        s, why = tt.series_from_table(header, rows, "x.csv")
        self.assertIsNone(why)
        self.assertEqual(s["info"]["col_v"], "voltage_v (exact)")
        self.assertEqual(s["info"]["col_i"], "current_a (exact)")
        self.assertEqual(s["info"]["col_r"], "internal_resistance_estimate (exact)")
        self.assertAlmostEqual(s["t"][1] - s["t"][0], 0.01)

    def test_units_in_brackets_and_semicolons(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.csv")
            with open(p, "w") as fh:
                fh.write("Time (ms);Voltage (V);Current (A)\n0;16.1;2.0\n10;16.1;2.0\n20;16.0;3.0\n")
            header, rows = tt.read_csv(p)
            s, why = tt.series_from_table(header, rows, "a.csv")
        self.assertIsNone(why)
        self.assertEqual(s["info"]["time_unit"], "0.001 s")
        self.assertAlmostEqual(s["t"][2], 0.02)
        self.assertEqual(s["v"], [16.1, 16.1, 16.0])
        self.assertEqual(s["i"], [2.0, 2.0, 3.0])

    def test_guessed_microseconds(self):
        s, _ = tt.series_from_table(["t", "voltage", "current"], [[0, 16, 2], [10000, 16, 2], [20000, 16, 2]], "b")
        self.assertEqual(s["info"]["time_unit"], "guessed 1e-06 s")
        self.assertAlmostEqual(s["t"][1], 0.01)

    def test_excluded_names(self):
        header = ["time_s", "voltage_prediction", "voltage_raw_adc", "bat_v", "current_a", "motor_0"]
        s, _ = tt.series_from_table(header, [[0.0, 1, 2, 16.0, 2.0, 0.1]], "c")
        self.assertEqual(s["info"]["col_v"], "bat_v (exact)")
        self.assertEqual(s["info"]["motor_columns"], ["motor_0"])


class TestAnalysis(unittest.TestCase):
    def test_offsets_and_lag(self):
        rep = tt.analyze(synth(lag=2))
        self.assertEqual(rep["rate_hz"], 100.0)
        self.assertEqual(rep["gaps"], 0)
        self.assertEqual(rep["steps_1a"], [{"k": 300, "t_s": 3.0, "di": 6.0, "offset": -2}])
        self.assertEqual(rep["step_offset_histogram"], {"-2": 1})
        self.assertEqual(rep["lag_all"]["best_lag"], 2)
        self.assertEqual(rep["lag_near_steps"]["best_lag"], 2)

    def test_aligned_has_zero_offset(self):
        rep = tt.analyze(synth(lag=0))
        self.assertEqual(rep["steps_1a"][0]["offset"], 0)
        self.assertEqual(rep["lag_all"]["best_lag"], 0)

    def test_current_late_is_positive(self):
        s = synth(lag=0)
        # voltage drops at 300, current only at 303: current late by 3
        s["i"] = [2.0 if k < 303 else 8.0 for k in range(len(s["t"]))]
        rep = tt.analyze(s)
        self.assertEqual(rep["steps_1a"][0]["offset"], 3)
        self.assertEqual(rep["lag_all"]["best_lag"], -3)

    def test_zero_stretch_and_spool(self):
        rep = tt.analyze(synth(lag=2))
        self.assertEqual(rep["zero_stretch_count"], 1)
        z = rep["zero_stretches"][0]
        self.assertEqual((z["start_k"], z["end_k"], z["from_trace_start"]), (303, 600, False))
        self.assertEqual(len(z["table"]), 21)
        row = [r for r in z["table"] if r["k"] == 300][0]
        self.assertEqual(row["implied_mohm"], 0.0)     # current stepped, voltage did not (yet)
        self.assertEqual(row["t_ms"], -30.0)
        sp = rep["spool_up"]
        self.assertEqual((sp["k"], sp["largest_dI_k"], sp["offset_samples"]), (300, 300, -2))
        self.assertEqual(rep["idle_current_a"], 2.0)

    def test_wrong_way_flag(self):
        s = synth(lag=0)
        for k in range(310, len(s["t"])):   # both step up together at 310: one wrong-way change
            s["v"][k] += 0.1
            s["i"][k] += 0.2
        rep = tt.analyze(s)
        self.assertEqual(rep["wrong_way_share"]["wrong_way"], 1)

    def test_gap_breaks_differences(self):
        s = synth(lag=0)
        for k in range(300, len(s["t"])):
            s["t"][k] += 0.5
        rep = tt.analyze(s)
        self.assertEqual(rep["gaps"], 1)
        self.assertEqual(rep["steps_1a"], [])   # the step spans the gap: not a step

    def test_too_short(self):
        s = synth(n=10, step_k=5, onset_k=6)
        self.assertIn("stopped", tt.analyze(s))


class TestMain(unittest.TestCase):
    def test_end_to_end(self):
        s = synth(lag=2)
        with tempfile.TemporaryDirectory() as d:
            data, out = os.path.join(d, "data"), os.path.join(d, "out")
            os.makedirs(data)
            with open(os.path.join(data, "flight1_battery_status_0.csv"), "w") as fh:
                fh.write("timestamp,voltage_v,current_a,internal_resistance_estimate\n")
                for k in range(len(s["t"])):
                    fh.write("%d,%.4f,%.3f,%.5f\n" % (round(s["t"][k] * 1e6), s["v"][k], s["i"][k], s["r"][k]))
            with open(os.path.join(data, "notes.md"), "w") as fh:
                fh.write("takeoff marker at 3.05 s\n")
            self.assertEqual(tt.main(["x", data, out]), 0)
            with open(os.path.join(out, "trace-timing.txt")) as fh:
                text = fh.read()
            with open(os.path.join(out, "trace-timing.json")) as fh:
                rep = json.load(fh)
        self.assertIn("estimate at 0 from k=303", text)
        self.assertIn("takeoff marker at 3.05 s", text)
        r = rep["files"]["flight1_battery_status_0.csv"]["report"]
        self.assertEqual(r["steps_1a"][0]["offset"], -2)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name.
"""ArduPilot's battery-resistance method, run on PX4-logged samples for comparison (PLAN-apcompare.md).

This is a re-implementation for analysis of the method used by ArduPilot's
AP_BattMonitor_Backend::update_resistance_estimate() (ArduPilot master read on 2026-11-16; ArduPilot is
GPL-3.0). It does not ship anything into ArduPilot or PX4. Per call, with voltage V and current I:

- calls with I below FLT_EPSILON change nothing;
- the largest current so far is kept; a call whose current equals the filtered current (within FLT_EPSILON)
  changes nothing else;
- the highest voltage so far is kept as a reference, together with its current;
- a per-call candidate r = -(V - V_filt) / (I - I_filt) is blended in only if it is positive:
  R <- R * (1 - w) + r * w, with w = min(1, 0.1 * |I - I_filt| / I_max);
- R is then capped by (V_ref - V) / (I - I_ref) whenever V < V_ref and I > I_ref;
- V_filt and I_filt follow a first-order low-pass with time constant 0.5 s (gain clamped to [0, 0.5]).

The state starts at zero. ArduPilot calls it at 10 Hz, after each battery read.

Stdlib only. Outcome names per call: 'no_current', 'no_step', 'blend' (positive candidate used),
'discard' (non-positive candidate dropped).
"""

import statistics

__version__ = "0.1.0"

FLT_EPSILON = 1.1920928955078125e-07
TC_1 = 0.5  # s, low-pass time constant of the filtered voltage and current
TC_2 = 0.1  # gain on |current step| / max current for the resistance blend


class APResistance:
    """State and one-call update of the ArduPilot-style estimator (resistance in Ohm per pack)."""

    def __init__(self, voltage_filt=0.0, current_filt=0.0, resistance=0.0, voltage_ref=0.0,
                 current_ref=0.0, current_max=0.0, timer_ms=0):
        self.voltage_filt = float(voltage_filt)
        self.current_filt = float(current_filt)
        self.resistance = float(resistance)
        self.voltage_ref = float(voltage_ref)
        self.current_ref = float(current_ref)
        self.current_max = float(current_max)
        self.timer_ms = int(timer_ms)
        self.last_r = None
        self.last_capped = False

    def update(self, now_ms, voltage, current):
        self.last_r = None
        self.last_capped = False
        if not current >= FLT_EPSILON:
            return "no_current"
        self.current_max = max(self.current_max, current)
        d_i = current - self.current_filt
        if abs(d_i) < FLT_EPSILON:
            return "no_step"
        if voltage > self.voltage_ref:
            self.voltage_ref = voltage
            self.current_ref = current
        dt = (int(now_ms) - self.timer_ms) * 0.001
        self.timer_ms = int(now_ms)
        alpha = min(max(dt / (dt + TC_1), 0.0), 0.5)
        weight = min(1.0, TC_2 * abs(d_i / self.current_max))
        r = -(voltage - self.voltage_filt) / d_i
        self.last_r = r
        outcome = "discard"
        if r >= FLT_EPSILON:
            self.resistance = self.resistance * (1.0 - weight) + r * weight
            outcome = "blend"
        if self.voltage_ref > voltage and current > self.current_ref:
            r_max = (self.voltage_ref - voltage) / (current - self.current_ref)
            if r_max < self.resistance:
                self.resistance = r_max
                self.last_capped = True
        self.voltage_filt = self.voltage_filt * (1.0 - alpha) + voltage * alpha
        self.current_filt = self.current_filt * (1.0 - alpha) + current * alpha
        return outcome


def parse_rows(dict_rows):
    """Rows of a PX4 battery CSV (time_from_takeoff_us, voltage_v, current_a, landed_last_reported, ...)."""
    out = []
    for d in dict_rows:
        out.append({
            "t_us": int(d["time_from_takeoff_us"]),
            "v": float(d["voltage_v"]),
            "i": float(d["current_a"]),
            "landed": int(d["landed_last_reported"]),
            "logged_r": float(d["internal_resistance_estimate"]),
        })
    return out


def block_means(rows, size=10):
    """Non-overlapping blocks of `size` consecutive rows from the first row; a short last block is dropped.
    Each block: mean voltage and current, and the time and landed flag of its last row."""
    out = []
    for k in range(0, len(rows) - size + 1, size):
        blk = rows[k:k + size]
        out.append({
            "t_us": blk[-1]["t_us"],
            "v": sum(r["v"] for r in blk) / size,
            "i": sum(r["i"] for r in blk) / size,
            "landed": blk[-1]["landed"],
        })
    return out


def to_ms(t_us, t0_us, boot_ms):
    """Millisecond clock: boot_ms at the file's first row, then whole milliseconds elapsed."""
    return boot_ms + (t_us - t0_us) // 1000


def run(samples, boot_ms=10000):
    """Feed samples (dicts with t_us, v, i, landed) to a fresh estimator; one trace entry per sample."""
    est = APResistance()
    trace = []
    if not samples:
        return trace
    t0 = samples[0]["t_us"]
    for s in samples:
        outcome = est.update(to_ms(s["t_us"], t0, boot_ms), s["v"], s["i"])
        trace.append({"t_us": s["t_us"], "landed": s["landed"], "outcome": outcome, "r": est.last_r,
                      "R": est.resistance, "capped": est.last_capped})
    return trace


def summarize(trace, n_cells, snapshots_s=(10, 20, 30)):
    """Derived numbers only. Resistances per cell in mOhm."""
    def mohm(r):
        return r / n_cells * 1000.0

    first_pos = next((e["t_us"] for e in trace if e["R"] > 0.0), None)
    first_air = next((e["t_us"] for e in trace if e["landed"] == 0), None)
    air = [e for e in trace if e["landed"] == 0]
    discards = sum(1 for e in air if e["outcome"] == "discard")
    blends = sum(1 for e in air if e["outcome"] == "blend")
    vals = [mohm(e["R"]) for e in air]
    snaps = {}
    for s in snapshots_s:
        hit = next((e for e in air if e["t_us"] >= s * 1000000), None)
        snaps["+%ds" % s] = mohm(hit["R"]) if hit is not None else None
    return {
        "calls": len(trace),
        "outcomes_all": {k: sum(1 for e in trace if e["outcome"] == k)
                         for k in ("no_current", "no_step", "blend", "discard")},
        "first_positive_t_us": first_pos,
        "first_airborne_t_us": first_air,
        "positive_before_takeoff": first_pos is not None and first_air is not None and first_pos < first_air,
        "airborne_n": len(air),
        "airborne_positive_n": sum(1 for e in air if e["R"] > 0.0),
        "airborne_discards": discards,
        "airborne_blends": blends,
        "discard_share_airborne": (discards / (discards + blends)) if (discards + blends) else None,
        "airborne_capped_n": sum(1 for e in air if e["capped"]),
        "airborne_mohm_per_cell_median": statistics.median(vals) if vals else None,
        "airborne_mohm_per_cell_min": min(vals) if vals else None,
        "airborne_mohm_per_cell_max": max(vals) if vals else None,
        "airborne_mohm_per_cell_final": vals[-1] if vals else None,
        "airborne_mohm_per_cell_at": snaps,
    }

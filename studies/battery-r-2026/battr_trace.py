"""battr_trace.py: follow PX4's battery internal-resistance estimate sample by sample through a
flight log recorded from boot with battery_status at full rate, and show which samples drove it to 0
(px4-log-health study battery-r-2026, trace, pre-registered in studies/battery-r-2026/PLAN-trace.md,
with the timing readouts of NOTE-trace-align.md added in 0.1.1, before any log was read).

Four parts, each usable alone:

1. Hidden raw R. battery_status.internal_resistance_estimate is clamped at 0, so a log never shows
   how far below 0 PX4's estimate went. But PX4 logs voltage_prediction on every update, and after a
   rejected update it sets its OCV estimate to V + R * I (R for the pack). So when the update at
   sample k was rejected (the logged covariance norm did not change) and sample k + 1 is the next
   update, voltage_prediction[k + 1] = V[k] + R * (I[k] - I[k + 1]), which gives the raw pack R.
   That needs consecutive firmware samples, i.e. a full-rate log. Where the logged estimate is
   positive, the recovered value must equal it, which checks exactly that.
2. Exact replay. It finds the last sample of PX4's 2 s reset window in the log (the last sample
   whose logged norm and prediction equal a fresh reset plus one update), and from there runs
   replay.py 0.1.0's port of Battery::updateInternalResistanceEstimation(). If the log holds every
   update, the replay equals the logged estimate, norm and prediction at every sample; the first
   sample where it does not is reported.
3. Attribution. From the last sample where the replayed R was at least 1 mOhm per cell to the first
   sample where it went below 0, every accepted update that lowered R is listed and classed as
   'quiet' (current within 0.1 A of the previous sample and within 0.3 A of the median of the
   previous second) or 'step', and for steps whether the voltage moved the same way as the current
   (which only a negative resistance, a timing offset or a sensor fault produces). Plus the lag at
   which voltage changes best mirror current changes.
4. Timing (0.1.1, NOTE-trace-align.md). A step update is also 'flat' if the voltage moved less than
   a quarter of what the current estimate expects: |dV| < 0.25 * n_cells * max(R_before, 1 mOhm) *
   |dI|. Every current change of at least 1 A between consecutive samples on the ground or in the
   first 5 s after takeoff gets a step offset: within +-5 samples, (sample of the largest current
   change in the step's direction) - (sample of the largest voltage change of the opposite sign).
   Positive means the current is late. (Note: the lag of part 3 has the opposite sign convention:
   a positive lag there means the voltage is late.) T4 and T5 of the note are graded from these.

    python battr_trace.py LOG.ulg [--instance N] [--json report.json]

Needs numpy, pyulog and replay.py 0.1.0 (px4-log-health, studies/battery-r-2026) next to it.
By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import argparse
import json
import math
import statistics
import sys

import numpy as np

import replay as rp

__version__ = "0.1.1"

ARMED = 2                   # vehicle_status ARMING_STATE_ARMED
R_PLAUSIBLE = 0.001         # Ohm per cell: the descent window starts at the last R at or above this
TOL_R = 2e-5                # Ohm per cell
TOL_NORM_REL = 1e-4
TOL_PRED = 2e-3             # V
ANCHOR_TOL_PRED = 1e-4      # V
ANCHOR_SLACK_US = 500000
QUIET_DI = 0.1              # A, change from the previous sample
QUIET_DEV = 0.3             # A, distance from the median of the previous second
QUIET_WINDOW_US = 1000000
RECOVER_MIN_DI = 0.2        # A
RECOVER_TOL = 5e-5          # Ohm per cell
RECOVER_MIN_R = 1e-4        # Ohm per cell: a logged estimate above this counts as positive
GAP_FACTOR = 1.5
MAX_LAG = 10
TOP = 10

# 0.1.1, NOTE-trace-align.md
FLAT_FRACTION = 0.25        # flat: |dV| below this share of n_cells * max(R_before, R_PLAUSIBLE) * |dI|
STEP_MIN_DI = 1.0           # A, a step for the offset readout and for T5
STEP_WINDOW = 5             # samples either side
AFTER_TAKEOFF_US = 5000000  # the offset readout covers the ground and the first 5 s after takeoff
T4_SHARE = 0.5
T5_BEFORE = 3               # the onset sample and the 3 samples before it

# the plan's gates
G1_MIN, G1_SHARE = 20, 0.90
G3_SHARE = 0.99


# ---------------------------------------------------------------- helpers

def updates(i):
    """PX4 updates the estimate only when current > FLT_EPSILON."""
    return math.isfinite(i) and i > rp.FLT_EPSILON


def predict(rls, v, i):
    """PX4's voltage_prediction for an update at (v, i): x^T * _RLS_est with x = [1, -I], float32,
    in replay.Rls's order of operations."""
    x0 = np.float32(1.0)
    x1 = -np.float32(i)
    return x0 * rls.e0 + x1 * rls.e1


def nominal_dt(t):
    d = np.diff(np.asarray(t, dtype=np.int64))
    d = d[d > 0]
    return int(np.median(d)) if len(d) else None


def consecutive(t, nominal):
    """c[k] is True if sample k follows sample k - 1 by more than 0 and at most GAP_FACTOR nominal
    intervals."""
    t = np.asarray(t, dtype=np.int64)
    c = np.zeros(len(t), dtype=bool)
    if nominal and len(t) > 1:
        d = np.diff(t)
        c[1:] = (d > 0) & (d <= GAP_FACTOR * nominal)
    return c


def usable(volt, conn=None):
    """Samples PX4 treats as a connected battery: finite voltage at or above 2.1 V (and the logged
    connected flag, if given)."""
    v = np.asarray(volt, dtype=np.float64)
    ok = np.isfinite(v) & (v >= rp.RECOGNITION_V)
    if conn is not None:
        ok &= np.asarray(conn).astype(bool)
    return ok


def quantiles(xs):
    xs = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not xs:
        return None

    def q(p):
        return round(xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))], 6)
    return {"n": len(xs), "min": round(xs[0], 6), "p10": q(0.10), "median": round(statistics.median(xs), 6),
            "p90": q(0.90), "max": round(xs[-1], 6)}


# ---------------------------------------------------------------- 1. hidden raw R

def recover_raw(t, volt, cur, log_r, log_norm, log_pred, n_cells, start=1):
    """Raw R per cell at rejected updates, from the logged prediction of the next sample. Rows are
    (k, raw); also how many agree with a positive logged estimate (the full-rate check)."""
    t = np.asarray(t, dtype=np.int64)
    cons = consecutive(t, nominal_dt(t))
    rows = []
    for k in range(max(start, 1), len(t) - 1):
        if not cons[k + 1]:
            continue
        ik, ik1 = float(cur[k]), float(cur[k + 1])
        if not (updates(ik) and updates(ik1)) or abs(ik - ik1) < RECOVER_MIN_DI:
            continue
        nk, nk0 = float(log_norm[k]), float(log_norm[k - 1])
        if not (math.isfinite(nk) and nk == nk0):
            continue
        p, v = float(log_pred[k + 1]), float(volt[k])
        if not (math.isfinite(p) and math.isfinite(v)):
            continue
        rows.append((k, (p - v) / (ik - ik1) / n_cells))
    pos = [(k, r) for k, r in rows if float(log_r[k]) > RECOVER_MIN_R]
    agree = sum(1 for k, r in pos if abs(r - float(log_r[k])) <= RECOVER_TOL)
    at_zero = [(k, r) for k, r in rows if float(log_r[k]) <= 0.0]
    return {"rows": rows, "positive": len(pos), "agree": agree,
            "share": round(agree / float(len(pos)), 4) if pos else None,
            "at_logged_zero": len(at_zero),
            "at_logged_zero_below_tol": sum(1 for _, r in at_zero if r <= RECOVER_TOL)}


def g1(rec):
    """Gate G1: the log holds consecutive firmware samples."""
    return rec["positive"] >= G1_MIN and rec["agree"] >= G1_SHARE * rec["positive"]


# ---------------------------------------------------------------- 2. exact replay

def find_anchor(t, volt, cur, log_norm, log_pred, n_cells, ok=None):
    """(index, candidates): the last sample, within 2.5 s of the first usable one, at which a fresh
    reset followed by that sample's update (if current > FLT_EPSILON) gives the logged covariance
    norm (relative 1e-4) and, if it updated, the logged voltage prediction (within 0.1 mV)."""
    t = np.asarray(t, dtype=np.int64)
    ok = usable(volt) if ok is None else ok
    idx = np.flatnonzero(ok)
    if not len(idx):
        return None, []
    first = int(idx[0])
    cands = []
    for k in range(first, len(t)):
        if int(t[k]) > int(t[first]) + rp.INIT_US + ANCHOR_SLACK_US:
            break
        if not ok[k]:
            continue
        v, i = float(volt[k]), float(cur[k])
        rls = rp.Rls(n_cells)
        rls.reset(v, i if math.isfinite(i) else -1.0)
        pred = None
        if updates(i):
            pred = float(predict(rls, v, i))
            rls.update(v, i)
        ln = float(log_norm[k])
        if not (math.isfinite(ln) and ln > 0 and abs(float(rls.norm) - ln) <= TOL_NORM_REL * ln):
            continue
        if pred is not None:
            lp = float(log_pred[k])
            if not (math.isfinite(lp) and abs(pred - lp) <= ANCHOR_TOL_PRED):
                continue
        cands.append(k)
    return (cands[-1] if cands else None), cands


def trace(t, volt, cur, n_cells, start, ok=None):
    """replay.Rls from a reset at `start` to the end of the log, stopping at the first unusable
    sample. Arrays aligned with the input (NaN / False outside the run): r_pub, r_raw, norm, pred
    (where it updated), d_raw (change of raw R per cell at the update), accepted, updated; and
    'stop' (index of the first unusable sample, or None)."""
    n = len(t)
    ok = usable(volt) if ok is None else ok
    out = {key: np.full(n, np.nan) for key in ("r_pub", "r_raw", "norm", "pred", "d_raw")}
    out["accepted"] = np.zeros(n, dtype=bool)
    out["updated"] = np.zeros(n, dtype=bool)
    out["stop"] = None
    rls = rp.Rls(n_cells)
    for k in range(start, n):
        if not ok[k]:
            out["stop"] = k
            break
        v, i = float(volt[k]), float(cur[k])
        if k == start:
            rls.reset(v, i if math.isfinite(i) else -1.0)
        if updates(i):
            before = float(rls.e1 / rls.n)
            out["pred"][k] = float(predict(rls, v, i))
            out["updated"][k] = True
            out["accepted"][k] = rls.update(v, i)
            out["d_raw"][k] = float(rls.e1 / rls.n) - before
        out["r_pub"][k] = float(rls.r_pub)
        out["r_raw"][k] = float(rls.e1 / rls.n)
        out["norm"][k] = float(rls.norm)
    return out


def agreement(tr, log_r, log_norm, log_pred, start, end):
    """Samples start..end (inclusive) the replay ran on: how many equal the log (R within TOL_R,
    norm within TOL_NORM_REL relative, and where it updated, prediction within TOL_PRED)."""
    n_all = n_ok = 0
    first_bad = None
    big = {"r": 0.0, "norm_rel": 0.0, "pred": 0.0}
    for k in range(start, end + 1):
        if not math.isfinite(tr["r_pub"][k]):
            continue
        n_all += 1
        dr = abs(tr["r_pub"][k] - float(log_r[k]))
        ln = float(log_norm[k])
        dn = abs(tr["norm"][k] - ln) / ln if (math.isfinite(ln) and ln > 0) else float("inf")
        dp = abs(tr["pred"][k] - float(log_pred[k])) if tr["updated"][k] else 0.0
        for key, x in (("r", dr), ("norm_rel", dn), ("pred", dp)):
            if math.isfinite(x):
                big[key] = max(big[key], float(x))
        good = (math.isfinite(dr) and math.isfinite(dn) and math.isfinite(dp)
                and dr <= TOL_R and dn <= TOL_NORM_REL and dp <= TOL_PRED)
        if good:
            n_ok += 1
        elif first_bad is None:
            first_bad = k
    return {"samples": n_all, "match": n_ok, "share": round(n_ok / float(n_all), 6) if n_all else None,
            "first_mismatch": first_bad, "max_diff": big}


# ---------------------------------------------------------------- 3. attribution

def descent_start(r_raw, start, onset):
    """The last sample in [start, onset) where the replayed raw R was at least R_PLAUSIBLE per
    cell, or start."""
    s = start
    for k in range(start, onset):
        if math.isfinite(r_raw[k]) and r_raw[k] >= R_PLAUSIBLE:
            s = k
    return s


def is_flat(dv, di, r_before, n_cells):
    """NOTE-trace-align.md: the voltage moved less than a quarter of what the current estimate
    expects for this current change."""
    return abs(dv) < FLAT_FRACTION * n_cells * max(r_before, R_PLAUSIBLE) * abs(di)


def attribute(t, volt, cur, tr, start, onset, n_cells=None):
    """Accepted updates in (descent start, onset] that lowered raw R, classed quiet / step and
    wrong-way (and, with n_cells, flat). Returns shares of the total decrease and the mechanism
    letter (PLAN-trace.md); the mechanism letter does not use 'flat'."""
    t = np.asarray(t, dtype=np.int64)
    s = descent_start(tr["r_raw"], start, onset)
    rows = []
    for k in range(s + 1, onset + 1):
        if not tr["accepted"][k] or not (tr["d_raw"][k] < 0):
            continue
        i, ip = float(cur[k]), float(cur[k - 1])
        lo = int(np.searchsorted(t, int(t[k]) - QUIET_WINDOW_US, side="left"))
        prev = np.asarray(cur[lo:k], dtype=np.float64)
        prev = prev[np.isfinite(prev)]
        med = float(np.median(prev)) if len(prev) else ip
        di = i - ip
        dv = float(volt[k]) - float(volt[k - 1])
        quiet = abs(di) < QUIET_DI and abs(i - med) < QUIET_DEV
        r_before = float(tr["r_raw"][k]) - float(tr["d_raw"][k])
        flat = bool(n_cells and (not quiet) and is_flat(dv, di, r_before, n_cells))
        rows.append({"k": int(k), "t_s": round((int(t[k]) - int(t[start])) / 1e6, 3),
                     "d_raw_mohm": float(tr["d_raw"][k]) * 1000.0, "r_raw_mohm": float(tr["r_raw"][k]) * 1000.0,
                     "r_before_mohm": r_before * 1000.0,
                     "current_a": i, "di_a": di, "dv_v": dv, "median_prev_1s_a": med,
                     "quiet": bool(quiet), "wrong_way": bool((not quiet) and dv * di > 0), "flat": flat})
    total = sum(-r["d_raw_mohm"] for r in rows)
    quiet = sum(-r["d_raw_mohm"] for r in rows if r["quiet"])
    wrong = sum(-r["d_raw_mohm"] for r in rows if r["wrong_way"])
    flat = sum(-r["d_raw_mohm"] for r in rows if r["flat"])
    either = sum(-r["d_raw_mohm"] for r in rows if r["flat"] or r["wrong_way"])
    step = total - quiet
    qs = quiet / total if total > 0 else None
    ws = wrong / step if step > 0 else None
    if qs is None:
        mech = None
    elif qs >= 0.5:
        mech = "A"
    elif ws is not None and ws >= 0.5:
        mech = "B"
    else:
        mech = "C"
    top = sorted(rows, key=lambda r: r["d_raw_mohm"])[:TOP]
    has_flat = bool(n_cells) and total > 0
    return {"descent_start": int(s), "onset": int(onset), "updates": len(rows),
            "decrease_mohm": round(total, 6), "quiet_share": round(qs, 4) if qs is not None else None,
            "step_share": round(1.0 - qs, 4) if qs is not None else None,
            "wrong_way_share_of_steps": round(ws, 4) if ws is not None else None,
            "mechanism": mech,
            "flat_share": round(flat / total, 4) if has_flat else None,
            "wrong_way_share": round(wrong / total, 4) if total > 0 else None,
            "flat_or_wrong_share": round(either / total, 4) if has_flat else None,
            "top": top, "rows": rows}


def lag_profile(volt, cur, mask=None, max_lag=MAX_LAG):
    """Correlation of the voltage change into sample k with the current change into sample k - L,
    L = -max_lag..max_lag (mask: which k count). Returns ({L: corr}, the L with the most negative
    correlation). A positive best L means the voltage responds L samples after the logged current."""
    v = np.asarray(volt, dtype=np.float64)
    i = np.asarray(cur, dtype=np.float64)
    dv = np.diff(v)
    di = np.diff(i)
    m = np.ones(len(dv), dtype=bool) if mask is None else np.asarray(mask, dtype=bool)[1:]
    prof = {}
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a, b, mm = dv[lag:], di[:len(di) - lag], m[lag:]
        else:
            a, b, mm = dv[:lag], di[-lag:], m[:lag]
        sel = mm & np.isfinite(a) & np.isfinite(b)
        if int(np.sum(sel)) < 10:
            continue
        aa, bb = a[sel], b[sel]
        if float(np.std(aa)) == 0.0 or float(np.std(bb)) == 0.0:
            continue
        prof[lag] = round(float(np.corrcoef(aa, bb)[0, 1]), 4)
    best = min(prof, key=lambda x: prof[x]) if prof else None
    return prof, best


# ---------------------------------------------------------------- 4. timing (0.1.1)

def changes(t, volt, cur, ok=None):
    """(dv, di, valid): dv[k] and di[k] are the voltage and current changes into sample k; valid[k]
    if samples k - 1 and k are consecutive in time, both usable and both changes finite."""
    t = np.asarray(t, dtype=np.int64)
    v = np.asarray(volt, dtype=np.float64)
    i = np.asarray(cur, dtype=np.float64)
    n = len(t)
    dv = np.full(n, np.nan)
    di = np.full(n, np.nan)
    if n > 1:
        dv[1:] = np.diff(v)
        di[1:] = np.diff(i)
    ok = usable(v) if ok is None else np.asarray(ok, dtype=bool)
    valid = consecutive(t, nominal_dt(t)) & np.isfinite(dv) & np.isfinite(di)
    if n > 1:
        valid[1:] &= ok[1:] & ok[:-1]
    if n:
        valid[0] = False
    return dv, di, valid


def step_offset(k, dv, di, valid, window=STEP_WINDOW):
    """For a current step into sample k: within k - window .. k + window (valid samples only), kc
    is the sample with the largest current change in the step's direction and kv the sample with
    the largest voltage change of the opposite sign (earliest on ties). offset = kc - kv (positive:
    the current is late), or None if no voltage change of the opposite sign is in the window."""
    n = len(di)
    sgn = 1.0 if di[k] > 0 else -1.0
    js = [j for j in range(max(1, k - window), min(n - 1, k + window) + 1) if valid[j]]
    kc = max(js, key=lambda j: (sgn * di[j], -j))
    opp = [j for j in js if -sgn * dv[j] > 0]
    kv = max(opp, key=lambda j: (-sgn * dv[j], -j)) if opp else None
    return {"k": int(k), "di_a": float(di[k]), "kc": int(kc), "kv": int(kv) if kv is not None else None,
            "dv_v": float(dv[kv]) if kv is not None else None,
            "offset": int(kc - kv) if kv is not None else None}


def step_offsets(t, volt, cur, ok=None, mask=None):
    """Every valid pair of consecutive samples (k - 1, k) with |dI| >= STEP_MIN_DI (and mask[k], if
    given), with its step offset. Returns the rows and a histogram of the offsets."""
    t = np.asarray(t, dtype=np.int64)
    dv, di, valid = changes(t, volt, cur, ok)
    sel = valid & (np.abs(np.nan_to_num(di, nan=0.0)) >= STEP_MIN_DI)
    if mask is not None:
        sel &= np.asarray(mask, dtype=bool)
    rows = []
    for k in np.flatnonzero(sel):
        r = step_offset(int(k), dv, di, valid)
        r["s_since_boot"] = round(int(t[k]) / 1e6, 3)
        rows.append(r)
    hist = {}
    for r in rows:
        key = "none" if r["offset"] is None else str(r["offset"])
        hist[key] = hist.get(key, 0) + 1
    return {"steps": len(rows), "histogram": hist, "rows": rows}


def t5_check(t, volt, cur, ok, onset):
    """T5 (NOTE-trace-align.md): among the current steps of at least 1 A into the onset sample or
    the 3 samples before it, take the largest (earliest on ties); T5 holds if its offset is not 0.
    Not graded if there is no such step, or if no voltage change of the opposite sign is within
    +-5 samples (then no offset can be read)."""
    dv, di, valid = changes(t, volt, cur, ok)
    ks = [k for k in range(max(1, onset - T5_BEFORE), onset + 1)
          if valid[k] and abs(di[k]) >= STEP_MIN_DI]
    if not ks:
        return {"graded": False, "held": None,
                "reason": "no current step of at least 1 A in the onset sample or the 3 before it"}
    k = max(ks, key=lambda j: (abs(di[j]), -j))
    so = step_offset(k, dv, di, valid)
    so["samples_before_onset"] = int(onset - k)
    if so["offset"] is None:
        return {"graded": False, "held": None, "step": so,
                "reason": "no voltage change of the opposite sign within 5 samples of the step"}
    return {"graded": True, "held": so["offset"] != 0, "step": so}


# ---------------------------------------------------------------- one log

def first_time(ts, values, pred):
    for tt, x in zip(ts, values):
        if pred(x):
            return int(tt)
    return None


def analyse(bat, n_cells, status=None, land=None):
    """bat: {field: array} of one battery_status instance (timestamp, voltage_v, current_a,
    internal_resistance_estimate, estimation_covariance_norm, voltage_prediction, optional
    connected). status: (timestamps, arming_state); land: (timestamps, landed). Returns the report."""
    rep = {"trace": __version__, "replay": rp.__version__, "n_cells": n_cells}
    need = ("timestamp", "voltage_v", "current_a", "internal_resistance_estimate",
            "estimation_covariance_norm", "voltage_prediction")
    missing = [k for k in need if k not in bat]
    if missing:
        rep["stop"] = "battery_status has no " + ", ".join(missing)
        return rep
    t = np.asarray(bat["timestamp"], dtype=np.int64)
    rep["samples"] = int(len(t))
    if not n_cells or len(t) < 3:
        rep["stop"] = "no cell count" if not n_cells else "too few samples"
        return rep
    volt = np.asarray(bat["voltage_v"], dtype=np.float64)
    cur = np.asarray(bat["current_a"], dtype=np.float64)
    log_r = np.asarray(bat["internal_resistance_estimate"], dtype=np.float64)
    log_norm = np.asarray(bat["estimation_covariance_norm"], dtype=np.float64)
    log_pred = np.asarray(bat["voltage_prediction"], dtype=np.float64)
    ok = usable(volt, bat.get("connected"))
    if not np.any(ok):
        rep["stop"] = "no connected sample"
        return rep
    nom = nominal_dt(t)
    cons = consecutive(t, nom)
    rep["first_sample_s_since_boot"] = round(int(t[0]) / 1e6, 3)
    rep["nominal_dt_ms"] = round(nom / 1000.0, 3) if nom else None
    rep["gaps"] = int(np.sum(~cons[1:]))

    armed_t = takeoff_t = None
    if status is not None:
        armed_t = first_time(status[0], status[1], lambda x: int(x) == ARMED)
    if land is not None:
        lt = [(int(a), bool(b)) for a, b in zip(land[0], land[1])]
        takeoff_t = next((a for a, b in lt if not b and (armed_t is None or a >= armed_t)), None)
    rep["armed_s_since_boot"] = round(armed_t / 1e6, 3) if armed_t is not None else None
    rep["takeoff_s_since_boot"] = round(takeoff_t / 1e6, 3) if takeoff_t is not None else None

    anchor, cands = find_anchor(t, volt, cur, log_norm, log_pred, n_cells, ok)
    start = anchor if anchor is not None else int(np.flatnonzero(ok)[0])
    rep["anchor"] = {"index": anchor, "s_since_boot": round(int(t[anchor]) / 1e6, 3) if anchor is not None else None,
                     "candidates": len(cands)}

    rec = recover_raw(t, volt, cur, log_r, log_norm, log_pred, n_cells, start=start + 1)
    raws = [r for _, r in rec["rows"]]
    rep["recovered"] = {k: rec[k] for k in rec if k != "rows"}
    rep["recovered"]["raw_mohm"] = quantiles([r * 1000.0 for r in raws])
    rep["recovered"]["most_negative"] = [
        {"k": int(k), "s_since_boot": round(int(t[k]) / 1e6, 3), "raw_mohm": round(r * 1000.0, 4)}
        for k, r in sorted(rec["rows"], key=lambda kr: kr[1])[:TOP]]

    tr = trace(t, volt, cur, n_cells, start, ok)
    end = (tr["stop"] - 1) if tr["stop"] is not None else len(t) - 1
    log_onset = next((k for k in range(start, end + 1) if log_r[k] <= 0.0), None)
    rep_onset = next((k for k in range(start, end + 1) if tr["r_raw"][k] < 0.0), None)
    ag = agreement(tr, log_r, log_norm, log_pred, start, log_onset if log_onset is not None else end)
    at_onset = (agreement(tr, log_r, log_norm, log_pred, log_onset, log_onset)["match"] == 1
                if log_onset is not None else None)
    rep["replay_stop"] = tr["stop"]
    rep["agreement"] = ag
    rep["onset_matches"] = at_onset

    def s_of(k):
        return round(int(t[k]) / 1e6, 3) if k is not None else None

    def rel(k, ref):
        return round((int(t[k]) - ref) / 1e6, 3) if (k is not None and ref is not None) else None

    rep["onset"] = {"logged_index": log_onset, "logged_s_since_boot": s_of(log_onset),
                    "logged_s_from_arming": rel(log_onset, armed_t),
                    "logged_s_from_takeoff": rel(log_onset, takeoff_t),
                    "replay_index": rep_onset, "replay_s_since_boot": s_of(rep_onset)}
    if takeoff_t is not None:
        k_to = int(np.searchsorted(t, takeoff_t, side="left"))
        air = log_r[k_to:]
        air = air[np.isfinite(air)]
        rep["zero_at_takeoff"] = bool(k_to > 0 and log_r[k_to - 1] <= 0.0)
        rep["zero_share_after_takeoff"] = round(float(np.mean(air <= 0.0)), 4) if len(air) else None
        upd = tr["updated"][k_to:]
        rep["replay_accept_share_after_takeoff"] = (
            round(float(np.sum(tr["accepted"][k_to:])) / float(np.sum(upd)), 4) if np.sum(upd) else None)

    gates = {"G1_full_rate": bool(g1(rec)), "G2_anchor": anchor is not None}
    gates["G3_replay"] = bool(gates["G2_anchor"] and ag["share"] is not None and ag["share"] >= G3_SHARE
                              and (log_onset is None or at_onset))
    rep["gates"] = gates
    rep["collapsed"] = log_onset is not None
    if log_onset is not None and rep_onset is not None and gates["G3_replay"]:
        rep["attribution"] = attribute(t, volt, cur, tr, start, rep_onset, n_cells)
        rep["attribution"]["s_since_boot"] = s_of(rep_onset)
    ground = cons.copy()
    if takeoff_t is not None:
        ground &= t < takeoff_t
    prof, best = lag_profile(volt, cur, ground)
    rep["lag_ground"] = {"best": best, "corr": prof}

    # 0.1.1: NOTE-trace-align.md readouts
    if takeoff_t is not None:
        so = step_offsets(t, volt, cur, ok, t < takeoff_t + AFTER_TAKEOFF_US)
        so["scope"] = "ground and the first 5 s after takeoff"
    else:
        so = step_offsets(t, volt, cur, ok)
        so["scope"] = "whole log (no takeoff time in the log)"
    rep["step_offsets"] = so
    graded = gates["G2_anchor"] and gates["G3_replay"]
    a = rep.get("attribution")
    if graded and a is not None and a.get("flat_or_wrong_share") is not None:
        rep["T4"] = {"graded": True, "held": a["flat_or_wrong_share"] >= T4_SHARE,
                     "flat_or_wrong_share": a["flat_or_wrong_share"], "flat_share": a["flat_share"],
                     "wrong_way_share": a["wrong_way_share"]}
    else:
        rep["T4"] = {"graded": False, "held": None,
                     "reason": "no collapse" if log_onset is None else
                     ("G2 or G3 failed" if not graded else "no decrease in the descent window")}
    if graded and log_onset is not None:
        rep["T5"] = t5_check(t, volt, cur, ok, log_onset)
    else:
        rep["T5"] = {"graded": False, "held": None,
                     "reason": "no collapse" if log_onset is None else "G2 or G3 failed"}
    return rep


def read_log(path, instance=None):
    """(battery_status fields, n_cells, status, land, instance, params) from a ULog, or None."""
    from pyulog import ULog
    u = ULog(path, ["battery_status", "vehicle_status", "vehicle_land_detected"], True)
    bats = {}
    status = land = None
    for d in u.data_list:
        if d.name == "battery_status":
            bats[d.multi_id] = d.data
        elif d.name == "vehicle_status" and d.multi_id == 0 and "arming_state" in d.data:
            status = (d.data["timestamp"], d.data["arming_state"])
        elif d.name == "vehicle_land_detected" and d.multi_id == 0 and "landed" in d.data:
            land = (d.data["timestamp"], d.data["landed"])
    if not bats:
        return None
    if instance is None:
        def score(m):
            return int(np.sum(usable(bats[m]["voltage_v"]))) if "voltage_v" in bats[m] else -1
        instance = max(sorted(bats), key=score)
    bat = bats[instance]
    p = dict(u.initial_parameters)
    n_cells = None
    if "cell_count" in bat:
        cc = np.asarray(bat["cell_count"], dtype=np.float64)
        cc = cc[cc > 0]
        if len(cc):
            n_cells = int(np.median(cc))
    if not n_cells:
        for name in ("BAT%d_N_CELLS" % (instance + 1), "BAT1_N_CELLS", "BAT_N_CELLS"):
            if p.get(name):
                n_cells = int(p[name])
                break
    params = {k: p.get(k) for k in ("SDLOG_MODE", "SDLOG_PROFILE", "BAT1_R_INTERNAL", "BAT1_N_CELLS",
                                    "BAT1_SOURCE", "BAT1_I_OVERWRITE")}
    return bat, n_cells, status, land, instance, params


def plain(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def summary_lines(rep):
    out = ["battr_trace %s (replay %s), %s samples, %s cells" % (rep["trace"], rep["replay"], rep.get("samples"),
                                                                rep.get("n_cells"))]
    if rep.get("stop"):
        out.append("stopped: %s" % rep["stop"])
        return out
    g = rep["gates"]
    out.append("first sample %.3f s after boot, nominal interval %s ms, %d gaps" % (
        rep["first_sample_s_since_boot"], rep["nominal_dt_ms"], rep["gaps"]))
    out.append("G1 full rate: %s (recovered R agrees with the positive logged estimate %s of %s times)" % (
        g["G1_full_rate"], rep["recovered"]["agree"], rep["recovered"]["positive"]))
    out.append("G2 reset window found: %s; G3 replay equals the log: %s (share %s, first mismatch %s)" % (
        g["G2_anchor"], g["G3_replay"], rep["agreement"]["share"], rep["agreement"]["first_mismatch"]))
    on = rep["onset"]
    if rep["collapsed"]:
        out.append("logged estimate first 0 at %s s after boot (%s s from arming, %s s from takeoff)" % (
            on["logged_s_since_boot"], on["logged_s_from_arming"], on["logged_s_from_takeoff"]))
    else:
        out.append("the logged estimate never reached 0")
    q = rep["recovered"].get("raw_mohm")
    if q:
        out.append("recovered raw R (mOhm per cell): min %s, median %s, max %s over %d rejected updates" % (
            q["min"], q["median"], q["max"], q["n"]))
    a = rep.get("attribution")
    if a:
        out.append("descent to 0: %d updates, quiet share %s, wrong-way share of steps %s -> mechanism %s" % (
            a["updates"], a["quiet_share"], a["wrong_way_share_of_steps"], a["mechanism"]))
        out.append("flat share %s, flat or wrong-way share %s of the decrease" % (
            a["flat_share"], a["flat_or_wrong_share"]))
    out.append("voltage/current lag on the ground: best L = %s samples (positive: voltage late)" %
               rep["lag_ground"]["best"])
    so = rep.get("step_offsets")
    if so is not None:
        out.append("current steps >= 1 A (%s): %d, offsets (positive: current late) %s" % (
            so["scope"], so["steps"], json.dumps(so["histogram"], sort_keys=True)))
    for name in ("T4", "T5"):
        p = rep.get(name)
        if p is not None:
            out.append("%s: %s" % (name, ("held" if p["held"] else "failed") if p["graded"]
                                   else "not graded (%s)" % p.get("reason")))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log")
    ap.add_argument("--instance", type=int, default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    got = read_log(args.log, args.instance)
    if got is None:
        rep = {"trace": __version__, "replay": rp.__version__, "stop": "no battery_status in the log"}
    else:
        bat, n_cells, status, land, inst, params = got
        rep = analyse(bat, n_cells, status, land)
        rep["instance"] = inst
        rep["params"] = params
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(json.dumps(rep, indent=1, default=plain) + "\n")
    print("\n".join(summary_lines(rep)), flush=True)
    return 0 if not rep.get("stop") else 2


if __name__ == "__main__":
    sys.exit(main())

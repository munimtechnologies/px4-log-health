"""trace_timing.py 0.1.0: timing readout for PX4 battery traces around motor spool-up and the
moments PX4's internal-resistance estimate reaches 0 (px4-log-health study battery-r-2026).

Exploratory, not the pre-registered grade: PLAN-trace.md's gates and T1-T5 need battr_trace.py's
exact replay on a from-boot ULog. This readout works on whatever a pilot can share (CSV exports
or ULog files) and reports, per trace:

- sampling: rate, median/p95/max interval, gaps;
- every stretch where the logged estimate is 0, and a sample table around each start
  (V, I, dV, dI, the pack resistance implied by that one step, -dV/dI, and 'wrong-way' when the
  voltage moved the same way as the current);
- motor spool-up (first sustained current rise of 1 A over the first second's median, and, if
  motor output columns exist, the first output rise) with a sample table and the offset between
  the largest current change and the largest opposite voltage change around it;
- NOTE-trace-align.md's step offsets for every |dI| >= 1 A step: within +-5 samples,
  (sample of the largest current change in the step's direction) - (sample of the largest voltage
  change of the opposite sign). Positive = the current is late;
- the lag at which voltage changes best mirror current changes (positive = the voltage is late).

    python trace_timing.py DATA_DIR OUT_DIR

Standard library only. ULog files are read with ulogmini.py next to this file (in px4-log-health it
lives in fixtures/; copy it here to read .ulg files). Written and published on 2026-11-14, before any
of the shared traces had been read.
Apache-2.0. Made with AI agents working in the name of MV2, Munim, Inc. (mv2@munimtech.com).
"""
import csv
import json
import math
import os
import statistics
import sys

VERSION = "0.1.0"
STEP_A = 1.0          # NOTE-trace-align.md step size
WINDOW = 5            # NOTE-trace-align.md +-5 samples
MIN_DI_IMPLIED = 0.05  # A: smallest current change used for an implied resistance
MAX_LAG = 10

TIME_EXACT = [("timestamp", 1e-6), ("timestamp_us", 1e-6), ("time_us", 1e-6), ("t_us", 1e-6),
              ("timestamp_ms", 1e-3), ("time_ms", 1e-3), ("t_ms", 1e-3), ("time_boot_ms", 1e-3),
              ("timestamp_s", 1.0), ("time_s", 1.0), ("t_s", 1.0), ("seconds", 1.0), ("elapsed_s", 1.0),
              ("time", None), ("t", None), ("elapsed", None)]
UNIT_SCALE = {"us": 1e-6, "usec": 1e-6, "µs": 1e-6, "ms": 1e-3, "msec": 1e-3,
              "s": 1.0, "sec": 1.0, "seconds": 1.0}
ROLES = {
    "v": (["voltage_v", "voltage", "v", "vbat", "bat_v", "batt_v", "battery_voltage"], ["volt"],
          ["filt", "pred", "adc", "raw", "cell", "avg", "average", "ocv", "drop", "rate"],
          ["pred", "adc", "cell", "ocv"]),
    "i": (["current_a", "current", "i", "ibat", "bat_i", "batt_i", "battery_current"], ["current", "curr"],
          ["filt", "avg", "average", "adc", "raw", "consumed", "mah", "est", "limit", "max"],
          ["adc", "consumed", "mah", "limit", "max"]),
    "r": (["internal_resistance_estimate", "internal_resistance", "r_internal", "resistance", "r_est"],
          ["resist"], [], None),
    "landed": (["landed", "land_detected", "is_landed"], [], [], None),
    "armed": (["armed", "is_armed", "arming_state"], [], [], None),
}
MOTOR_HINTS = ("motor", "output", "pwm", "actuator", "esc", "throttle")


# ---------------------------------------------------------------- reading
def norm(name):
    """'Voltage (V)' -> ('voltage', 'v'); 'current_a' -> ('current_a', '')."""
    s = name.strip().lower().replace("[", "(").replace("]", ")")
    unit = ""
    if "(" in s and not s.startswith("("):
        k = s.index("(")
        inner = s[k + 1:].rstrip(")").strip()
        # 'output(0)' style indices are not units
        if not inner.isdigit():
            unit = inner
            s = s[:k].strip()
    return s.replace(" ", "_").replace("-", "_"), unit


def pick(cols, role, taken):
    exact, contains, exclude, fallback_exclude = ROLES[role]
    for name in exact:
        for idx, (_, nm, _) in enumerate(cols):
            if nm == name and idx not in taken:
                return idx, "exact"
    for excl, how in ((exclude, "contains"), (fallback_exclude, "fallback")):
        if excl is None or not contains:
            continue
        for idx, (_, nm, _) in enumerate(cols):
            if idx in taken:
                continue
            if any(c in nm for c in contains) and not any(x in nm for x in excl):
                return idx, how
    return None, None


def pick_time(cols):
    for name, scale in TIME_EXACT:
        for idx, (_, nm, unit) in enumerate(cols):
            if nm == name:
                return idx, UNIT_SCALE.get(unit, scale)
    for idx, (_, nm, unit) in enumerate(cols):
        if "time" in nm:
            if unit in UNIT_SCALE:
                return idx, UNIT_SCALE[unit]
            if nm.endswith("_us"):
                return idx, 1e-6
            if nm.endswith("_ms"):
                return idx, 1e-3
            return idx, None
    return None, None


def to_float(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def read_csv(path):
    """-> (header, rows as lists of float or None)."""
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        text = fh.read()
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return [], []
    first = lines[0]
    delim = max([",", ";", "\t"], key=first.count)
    reader = csv.reader(lines, delimiter=delim)
    header = next(reader)
    rows = [[to_float(x) for x in row] for row in reader]
    return [h.strip() for h in header], rows


def guess_scale(raw_t):
    diffs = [b - a for a, b in zip(raw_t, raw_t[1:]) if b > a]
    if not diffs:
        return 1.0
    d = statistics.median(diffs)
    if d >= 1000:
        return 1e-6
    if d >= 2:
        return 1e-3
    return 1.0


def series_from_table(header, rows, source):
    """Pick columns and build a trace dict, or (None, reason)."""
    cols = [(h,) + norm(h) for h in header]
    info = {"source": source, "columns": header}
    ti, scale = pick_time(cols)
    if ti is None:
        return None, "no time column"
    taken = {ti}
    picked = {}
    for role in ("v", "i", "r", "landed", "armed"):
        idx, how = pick(cols, role, taken)
        if idx is not None:
            taken.add(idx)
            picked[role] = idx
            info["col_" + role] = "%s (%s)" % (header[idx], how)
    motors = [idx for idx, (_, nm, _) in enumerate(cols)
              if idx not in taken and any(h in nm for h in MOTOR_HINTS)]
    info["col_t"] = header[ti]
    info["motor_columns"] = [header[m] for m in motors]
    raw_t = [r[ti] if ti < len(r) else None for r in rows]
    if scale is None:
        scale = guess_scale([x for x in raw_t if x is not None])
        info["time_unit"] = "guessed %g s" % scale
    else:
        info["time_unit"] = "%g s" % scale
    out = {"t": [], "v": [], "i": [], "r": [], "landed": [], "armed": [], "motors": [[] for _ in motors]}
    for r, tr in zip(rows, raw_t):
        def get(role):
            idx = picked.get(role)
            return r[idx] if idx is not None and idx < len(r) else None
        if tr is None:
            continue
        out["t"].append(tr * scale)
        for role in ("v", "i", "r", "landed", "armed"):
            out[role].append(get(role))
        for k, m in enumerate(motors):
            out["motors"][k].append(r[m] if m < len(r) else None)
    out["info"] = info
    out["motor_names"] = info["motor_columns"]
    if not out["t"]:
        return None, "no rows with a time value"
    return out, None


def series_from_ulog(path):
    import ulogmini
    u = ulogmini.ULog(path, ["battery_status", "vehicle_status", "vehicle_land_detected", "actuator_motors",
                             "actuator_outputs"])
    bats = {d.multi_id: d.data for d in u.data_list if d.name == "battery_status"}
    if not bats:
        return None, "no battery_status in the ULog"

    def score(m):
        return sum(1 for x in bats[m].get("voltage_v", []) if x is not None and x >= 2.1)
    inst = max(sorted(bats), key=score)
    b = bats[inst]
    n = len(b.get("timestamp", []))
    out = {"t": [x * 1e-6 for x in b["timestamp"]],
           "v": list(b.get("voltage_v", [None] * n)), "i": list(b.get("current_a", [None] * n)),
           "r": list(b.get("internal_resistance_estimate", [None] * n)),
           "landed": [None] * n, "armed": [None] * n, "motors": [], "motor_names": []}
    events = {}
    for d in u.data_list:
        if d.name == "vehicle_status" and d.multi_id == 0 and "arming_state" in d.data:
            for ts, a in zip(d.data["timestamp"], d.data["arming_state"]):
                if a == 2:
                    events["armed_t"] = ts * 1e-6
                    break
        if d.name == "vehicle_land_detected" and d.multi_id == 0 and "landed" in d.data:
            prev = None
            for ts, la in zip(d.data["timestamp"], d.data["landed"]):
                if prev == 1 and la == 0:
                    events["takeoff_t"] = ts * 1e-6
                    break
                prev = la
        if d.name == "actuator_motors" and d.multi_id == 0 and "control[0]" in d.data:
            ts = d.data["timestamp"]
            c0 = d.data["control[0]"]
            t_m = motor_rise_time([x * 1e-6 for x in ts], c0)
            if t_m is not None:
                events["motor_rise_t"] = t_m
    out["events"] = events
    out["info"] = {"source": os.path.basename(path), "battery_instance": inst,
                   "params": {k: u.initial_parameters.get(k) for k in
                              ("BAT1_R_INTERNAL", "BAT1_N_CELLS", "BAT1_SOURCE", "SDLOG_PROFILE", "SDLOG_MODE")},
                   "ulog_topics": sorted({d.name for d in u.data_list})}
    return out, None


# ---------------------------------------------------------------- analysis
def diffs(x, ok):
    return [(x[k + 1] - x[k]) if ok[k] and x[k] is not None and x[k + 1] is not None else None
            for k in range(len(x) - 1)]


def pearson(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sa = sum((x - ma) ** 2 for x in a)
    sb = sum((y - mb) ** 2 for y in b)
    if sa <= 0 or sb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(sa * sb)


def lag_scan(dI, dV, only=None):
    """corr(dI[m], -dV[m + L]) for L in -MAX_LAG..MAX_LAG. Positive best L = the voltage is late."""
    res = {}
    for lag in range(-MAX_LAG, MAX_LAG + 1):
        a, b = [], []
        for m in range(len(dI)):
            j = m + lag
            if 0 <= j < len(dV) and dI[m] is not None and dV[j] is not None:
                if only is not None and m not in only:
                    continue
                a.append(dI[m])
                b.append(-dV[j])
        res[lag] = pearson(a, b)
    valid = {k: v for k, v in res.items() if v is not None}
    best = max(valid, key=lambda k: (valid[k], -abs(k))) if valid else None
    return best, res


def step_offset(dI, dV, m, lo_off=-WINDOW, hi_off=WINDOW):
    """Offset at the step whose change is dI[m]: (largest current change in its direction) -
    (largest voltage change of the opposite sign), within m+lo_off..m+hi_off. None if no voltage change
    of the opposite sign exists there."""
    s = 1.0 if dI[m] > 0 else -1.0
    lo, hi = max(0, m + lo_off), min(len(dI) - 1, m + hi_off)
    ic = iv = None
    best_i = best_v = 0.0
    for j in range(lo, hi + 1):
        if dI[j] is not None and (ic is None or s * dI[j] > best_i):
            ic, best_i = j, s * dI[j]
        if dV[j] is not None and -s * dV[j] > best_v:
            iv, best_v = j, -s * dV[j]
    if ic is None or iv is None:
        return None
    return ic - iv


def motor_rise_time(t, x):
    vals = [(a, b) for a, b in zip(t, x)
            if a is not None and isinstance(b, (int, float)) and math.isfinite(b)]
    if len(vals) < 5:
        return None
    t0 = vals[0][0]
    first = [b for a, b in vals if a - t0 <= 1.0]
    lo, hi = min(b for _, b in vals), max(b for _, b in vals)
    if hi - lo <= 0:
        return None
    base = statistics.median(first)
    for a, b in vals:
        if b > base + 0.1 * (hi - lo):
            return a
    return None


def spool_index(t, cur, ok_rows):
    idx = [k for k in ok_rows]
    if len(idx) < 10:
        return None, None
    t0 = t[idx[0]]
    first = [cur[k] for k in idx if t[k] - t0 <= 1.0]
    idle = statistics.median(first)
    for n, k in enumerate(idx):
        if cur[k] >= idle + 1.0:
            if t[idx[-1]] - t[k] < 0.2:
                break
            held = True
            for j in idx[n:]:
                if t[j] - t[k] > 0.2:
                    break
                if cur[j] < idle + 0.5:
                    held = False
                    break
            if held:
                return k, idle
    return None, idle


def table(s, k0, before, after, dI, dV, t_ref):
    rows = []
    n = len(s["t"])
    for k in range(max(0, k0 - before), min(n, k0 + after + 1)):
        di = dI[k - 1] if k >= 1 else None
        dv = dV[k - 1] if k >= 1 else None
        implied = None
        if di is not None and dv is not None and abs(di) >= MIN_DI_IMPLIED:
            implied = -dv / di * 1000.0
        rows.append({
            "k": k, "t_ms": round((s["t"][k] - t_ref) * 1000.0, 1),
            "v": s["v"][k], "i": s["i"][k], "dv": None if dv is None else round(dv, 4),
            "di": None if di is None else round(di, 4),
            "implied_mohm": None if implied is None else round(implied, 1),
            "wrong_way": bool(di is not None and dv is not None and abs(di) >= MIN_DI_IMPLIED and dv * di > 0),
            "r": s["r"][k]})
    return rows


def analyze(s):
    t, v, cur = s["t"], s["v"], s["i"]
    n = len(t)
    rep = {"n": n}
    ok_rows = [k for k in range(n) if v[k] is not None and cur[k] is not None]
    rep["rows_with_v_and_i"] = len(ok_rows)
    if len(ok_rows) < 20:
        rep["stopped"] = "fewer than 20 rows with both voltage and current"
        return rep
    dts = [t[k + 1] - t[k] for k in range(n - 1)]
    pos = [d for d in dts if d > 0]
    med = statistics.median(pos) if pos else 0.0
    rep["duration_s"] = round(t[-1] - t[0], 3)
    rep["dt_median_ms"] = round(med * 1000, 3)
    if pos:
        q = sorted(pos)
        rep["dt_p95_ms"] = round(q[int(0.95 * (len(q) - 1))] * 1000, 3)
        rep["dt_max_ms"] = round(q[-1] * 1000, 3)
        rep["rate_hz"] = round(1.0 / med, 2) if med > 0 else None
    consecutive = [0 < d <= 1.5 * med for d in dts]
    rep["gaps"] = sum(1 for c in consecutive if not c)
    dI, dV = diffs(cur, consecutive), diffs(v, consecutive)

    ev = dict(s.get("events", {}))
    landed, armed = s.get("landed", []), s.get("armed", [])
    if "takeoff_t" not in ev:
        prev = None
        for k in range(n):
            la = landed[k] if k < len(landed) else None
            if la is None:
                continue
            if prev is not None and prev >= 0.5 and la < 0.5:
                ev["takeoff_t"] = t[k]
                break
            prev = la
    if "armed_t" not in ev:
        boolean = all(x in (None, 0, 1) for x in armed)
        for k in range(n):
            a = armed[k] if k < len(armed) else None
            if a is not None and (a == 2 or (a == 1 and boolean)):
                ev["armed_t"] = t[k]
                break
    for mi, name in enumerate(s.get("motor_names", [])):
        tm = motor_rise_time(t, s["motors"][mi])
        if tm is not None and ("motor_rise_t" not in ev or tm < ev["motor_rise_t"]):
            ev["motor_rise_t"] = tm
            ev["motor_rise_column"] = name
    rep["events_s_from_start"] = {k: (round(x - t[0], 4) if isinstance(x, float) else x) for k, x in ev.items()}

    # stretches where the logged estimate is 0
    r = s.get("r") or []
    zero = []
    if any(x is not None for x in r):
        k = 0
        while k < n:
            if r[k] is not None and r[k] <= 0.0:
                j = k
                while j + 1 < n and (r[j + 1] is None or r[j + 1] <= 0.0):
                    j += 1
                zero.append((k, j))
                k = j + 1
            else:
                k += 1
        rep["r_present"] = True
        pos_r = [x for x in r if x is not None and x > 0]
        rep["r_positive_median_mohm"] = round(statistics.median(pos_r) * 1000, 3) if pos_r else None
    else:
        rep["r_present"] = False
    rep["zero_stretches"] = []
    for a, b in zero[:20]:
        item = {"start_k": a, "end_k": b, "start_s": round(t[a] - t[0], 4),
                "duration_ms": round((t[b] - t[a]) * 1000 + med * 1000, 1),
                "from_trace_start": a == 0}
        for name in ("takeoff_t", "armed_t", "motor_rise_t"):
            if name in ev and isinstance(ev[name], float):
                item["start_minus_" + name[:-2] + "_ms"] = round((t[a] - ev[name]) * 1000, 1)
        if a > 0:
            item["table"] = table(s, a, 10, 10, dI, dV, t[a])
        rep["zero_stretches"].append(item)
    rep["zero_stretch_count"] = len(zero)

    # spool-up
    k_sp, idle = spool_index(t, cur, ok_rows)
    rep["idle_current_a"] = None if idle is None else round(idle, 3)
    if k_sp is not None:
        sp = {"k": k_sp, "t_s": round(t[k_sp] - t[0], 4)}
        cands = [m for m in range(max(0, k_sp - 5), min(len(dI), k_sp + 15)) if dI[m] is not None]
        if cands:
            m_big = max(cands, key=lambda m: dI[m])
            sp["largest_dI_k"] = m_big + 1
            sp["largest_dI_a"] = round(dI[m_big], 4)
            sp["offset_samples"] = step_offset(dI, dV, m_big)
        for name in ("takeoff_t", "armed_t", "motor_rise_t"):
            if name in ev and isinstance(ev[name], float):
                sp["minus_" + name[:-2] + "_ms"] = round((t[k_sp] - ev[name]) * 1000, 1)
        sp["table"] = table(s, k_sp, 10, 20, dI, dV, t[k_sp])
        rep["spool_up"] = sp

    # NOTE-trace-align step offsets
    steps = []
    for m in range(len(dI)):
        if dI[m] is not None and abs(dI[m]) >= STEP_A:
            steps.append({"k": m + 1, "t_s": round(t[m + 1] - t[0], 4), "di": round(dI[m], 3),
                          "offset": step_offset(dI, dV, m)})
    rep["steps_1a"] = steps[:200]
    hist = {}
    for st in steps:
        key = "none" if st["offset"] is None else str(st["offset"])
        hist[key] = hist.get(key, 0) + 1
    rep["step_offset_histogram"] = dict(sorted(hist.items()))

    best, res = lag_scan(dI, dV)
    rep["lag_all"] = {"best_lag": best, "corr": {str(k): (None if x is None else round(x, 3)) for k, x in res.items()}}
    near = set()
    for m in range(len(dI)):
        if dI[m] is not None and abs(dI[m]) >= 0.5:
            near.update(range(max(0, m - WINDOW), m + WINDOW + 1))
    if near:
        best2, res2 = lag_scan(dI, dV, near)
        rep["lag_near_steps"] = {"best_lag": best2, "n_steps_0_5a": sum(1 for x in dI if x is not None and abs(x) >= 0.5),
                                 "corr": {str(k): (None if x is None else round(x, 3)) for k, x in res2.items()}}
    ww = [m for m in range(len(dI)) if dI[m] is not None and dV[m] is not None and abs(dI[m]) >= MIN_DI_IMPLIED
          and dV[m] * dI[m] > 0]
    big = [m for m in range(len(dI)) if dI[m] is not None and dV[m] is not None and abs(dI[m]) >= MIN_DI_IMPLIED]
    rep["wrong_way_share"] = {"wrong_way": len(ww), "of_changes_ge_0_05a": len(big)}
    return rep


# ---------------------------------------------------------------- report
def fmt_table(rows):
    out = ["      k     t_ms        V        I       dV       dI  -dV/dI mOhm  ww  R"]
    for r in rows:
        def f(x, w, p):
            return ("%*.*f" % (w, p, x)) if isinstance(x, (int, float)) else " " * (w - 1) + "-"
        out.append("  %5d %8.1f %8s %8s %8s %8s %12s  %s  %s" % (
            r["k"], r["t_ms"], f(r["v"], 8, 3), f(r["i"], 8, 3), f(r["dv"], 8, 4), f(r["di"], 8, 4),
            f(r["implied_mohm"], 12, 1), "W " if r["wrong_way"] else "  ",
            "-" if r["r"] is None else "%.5g" % r["r"]))
    return out


def render(name, info, rep):
    out = ["", "=" * 78, "%s" % name, "=" * 78]
    for k in sorted(info):
        if k in ("columns",):
            continue
        out.append("  %s: %s" % (k, info[k]))
    if "columns" in info:
        out.append("  columns: %s" % ", ".join(info["columns"][:60]))
    for k in ("n", "rows_with_v_and_i", "duration_s", "rate_hz", "dt_median_ms", "dt_p95_ms", "dt_max_ms", "gaps",
              "idle_current_a", "r_present", "r_positive_median_mohm", "zero_stretch_count", "stopped",
              "events_s_from_start", "step_offset_histogram", "wrong_way_share"):
        if k in rep:
            out.append("  %s: %s" % (k, rep[k]))
    for key in ("lag_all", "lag_near_steps"):
        if key in rep:
            out.append("  %s: best lag %s; corr %s" % (key, rep[key]["best_lag"], rep[key]["corr"]))
    for z in rep.get("zero_stretches", []):
        out.append("")
        out.append("  -- estimate at 0 from k=%d (%.3f s) for %.1f ms%s" % (
            z["start_k"], z["start_s"], z["duration_ms"], " (from trace start)" if z["from_trace_start"] else ""))
        for k in sorted(z):
            if k.startswith("start_minus"):
                out.append("     %s: %s" % (k, z[k]))
        if "table" in z:
            out.extend("   " + ln for ln in fmt_table(z["table"]))
    sp = rep.get("spool_up")
    if sp:
        out.append("")
        out.append("  -- spool-up at k=%d (%.3f s)" % (sp["k"], sp["t_s"]))
        for k in sorted(sp):
            if k not in ("table", "k", "t_s"):
                out.append("     %s: %s" % (k, sp[k]))
        out.extend("   " + ln for ln in fmt_table(sp["table"]))
    if rep.get("steps_1a"):
        out.append("")
        out.append("  -- |dI| >= 1 A steps (first 40): k, t_s, dI, offset")
        for st in rep["steps_1a"][:40]:
            out.append("     %6d %9.3f %7.3f %s" % (st["k"], st["t_s"], st["di"], st["offset"]))
    return out


def dump_file(path):
    """A short look at any file, so its layout is known even if no trace was found."""
    lines = ["", "-- file %s (%d bytes)" % (os.path.basename(path), os.path.getsize(path))]
    low = path.lower()
    if low.endswith((".csv", ".tsv", ".txt", ".md", ".json", ".params", ".param")):
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read(200000)
        all_lines = text.splitlines()
        lines.append("   lines (first 200 kB): %d" % len(all_lines))
        limit = 60 if low.endswith((".txt", ".md")) else 4
        for ln in all_lines[:limit]:
            lines.append("   | " + ln[:400])
    elif low.endswith(".ulg"):
        try:
            import ulogmini
            u = ulogmini.ULog(path)
            lines.append("   ulog topics: %s" % ", ".join("%s/%d(%d)" % (d.name, d.multi_id, len(d.data.get("timestamp", [])))
                                                         for d in u.data_list))
        except Exception as exc:  # report, never stop
            lines.append("   ulogmini failed: %s: %s" % (type(exc).__name__, exc))
    return lines


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    data, out_dir = argv[1], argv[2]
    os.makedirs(out_dir, exist_ok=True)
    files = []
    for root, _, names in os.walk(data):
        for nm in sorted(names):
            files.append(os.path.join(root, nm))
    files.sort()
    text = ["trace_timing %s: %d files under %s" % (VERSION, len(files), data),
            "Exploratory readout, not the pre-registered PLAN-trace grade."]
    reports = {"version": VERSION, "files": {}}
    for path in files:
        rel = os.path.relpath(path, data)
        try:
            text.extend(dump_file(path))
        except Exception as exc:
            text.append("   dump failed: %s" % exc)
        s, why = None, None
        try:
            if path.lower().endswith(".ulg"):
                s, why = series_from_ulog(path)
            elif path.lower().endswith((".csv", ".tsv", ".txt")):
                header, rows = read_csv(path)
                if header and rows:
                    s, why = series_from_table(header, rows, rel)
                else:
                    why = "empty"
            else:
                continue
        except Exception as exc:
            why = "%s: %s" % (type(exc).__name__, exc)
        if s is None:
            reports["files"][rel] = {"skipped": why}
            text.append("   not a trace: %s" % why)
            continue
        if not any(x is not None for x in s["v"]) or not any(x is not None for x in s["i"]):
            info = s["info"]
            ev = {}
            for mi, name in enumerate(s.get("motor_names", [])):
                tm = motor_rise_time(s["t"], s["motors"][mi])
                if tm is not None:
                    ev[name] = tm
            reports["files"][rel] = {"info": info, "motor_rise_abs_s": ev, "skipped": "no voltage/current columns"}
            text.append("   no voltage/current columns; motor rise times (absolute s): %s" % ev)
            continue
        rep = analyze(s)
        reports["files"][rel] = {"info": s["info"], "report": rep, "t0_abs_s": s["t"][0]}
        text.extend(render(rel, s["info"], rep))
    with open(os.path.join(out_dir, "trace-timing.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(text) + "\n")
    with open(os.path.join(out_dir, "trace-timing.json"), "w", encoding="utf-8") as fh:
        json.dump(reports, fh, indent=1, default=str)
    print("\n".join(text[:400]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

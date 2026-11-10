"""battery_r.py: did PX4's battery-resistance estimate collapse to 0 in this flight? (px4-log-health)

Since PX4 PR #23205 (merged 2024-07-02), PX4's battery library estimates each pack's internal
resistance online (recursive least squares on V = V_oc - R * I) and, when BAT1_R_INTERNAL is -1
(the default), uses it to correct the voltage-based state of charge for the voltage drop under
load. A negative estimate is published as 0. The pre-registered census battery-r-2026
(studies/battery-r-2026/GRADE.md) found the published estimate at exactly 0 for the whole second
half of the flight on 10 of 75 measurable public PX4 flights; 9 of them had BAT1_R_INTERNAL = -1,
so their state of charge was not corrected for load (it reads low under load and recovers after
landing).

This check measures one ULog exactly as the census did (battr_logs.py 0.1.0, copied unchanged) and
says whether the estimate collapsed and whether PX4 was using it. It suggests no resistance value.

    python battery_r.py flight.ulg [more.ulg ...] [--json]

Needs numpy and pyulog. By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents.
Apache-2.0.
"""

import argparse
import json
import sys

__version__ = "0.1.0"

DOCS = "https://docs.px4.io/main/en/config/battery"
TOPICS = ("battery_status", "vehicle_land_detected", "vehicle_status")
FIELD = "internal_resistance_estimate"
COV = "estimation_covariance_norm"

# one log's measurement (as in the census, battr_logs.py 0.1.0)
MIN_SAMPLES = 150           # 30 s of airborne samples at the default 5 Hz
MIN_CURRENT_A = 0.5
EARLY = 0.25                # "froze early": last change in the first quarter of airborne samples
MAX_GAP_S = 0.5
DATA_MIN_PAIRS = 150
DATA_MIN_RANGE_A = 5.0
DATA_MAX_R = -0.3           # the step fit counts only if dV and dI are this anti-correlated

# battery-r-2026 (battr-summary.json, graded 2026-11-10)
FLEET = {"source": "battery-r-2026: 75 measurable public PX4 flights (Flight Review, read 2026-11-10)",
         "n": 75, "collapsed": 10, "used_estimate": 53, "collapsed_using_estimate": 9,
         "alive_n": 65, "alive_p10": 1.1656, "alive_p50": 2.7357, "alive_p90": 7.4914}


# ---------------------------------------------------------------- copied from battr_logs.py 0.1.0

def airborne_mask(t, land=None, status=None):
    """(mask, source): True at each time in `t` (us) while airborne. land: (timestamps, landed) from
    vehicle_land_detected; status: (timestamps, arming_state) from vehicle_status, used only without
    land (armed = 2). (None, 'none') if neither is given."""
    import numpy as np
    t = np.asarray(t, dtype=np.int64)
    if land is not None and len(land[0]):
        ts = np.asarray(land[0], dtype=np.int64)
        flying = np.asarray(land[1]).astype(np.int64) == 0
        source = "land_detected"
    elif status is not None and len(status[0]):
        ts = np.asarray(status[0], dtype=np.int64)
        flying = np.asarray(status[1]).astype(np.int64) == 2
        source = "armed"
    else:
        return None, "none"
    idx = np.searchsorted(ts, t, side="right") - 1
    ok = idx >= 0
    mask = np.zeros(len(t), dtype=bool)
    mask[ok] = flying[idx[ok]]
    return mask, source


def estimator_active(b):
    """True when this battery_status instance carries a running RLS estimator: both fields present,
    and the covariance norm is positive somewhere and takes at least two values."""
    import numpy as np
    if FIELD not in b or COV not in b:
        return False
    cov = np.asarray(b[COV], dtype=np.float64)
    cov = cov[np.isfinite(cov)]
    return bool(len(cov)) and float(np.max(cov)) > 0.0 and len(np.unique(cov)) >= 2


def pick_battery(bats):
    """multi_id of the instance with an active estimator and the highest median valid current."""
    import numpy as np
    best = None
    for mid in sorted(bats):
        b = bats[mid]
        if "current_a" not in b or "timestamp" not in b or not estimator_active(b):
            continue
        c = np.asarray(b["current_a"], dtype=np.float64)
        ok = np.isfinite(c) & (c >= 0)
        if int(np.sum(ok)) < 2:
            continue
        med = float(np.median(c[ok]))
        if best is None or med > best[0]:
            best = (med, mid)
    return None if best is None else best[1]


def last_change_frac(v):
    """Where the last change of `v` falls, as a fraction of its samples (0 = never changed)."""
    n = len(v)
    last = 0
    for i in range(1, n):
        if v[i] != v[i - 1]:
            last = i
    return 0.0 if n < 2 else last / float(n - 1)


def step_fit(t, volt, cur, n_cells):
    """Pack resistance from consecutive-sample steps: dV = -R * dI (least squares through the
    origin), over pairs at most MAX_GAP_S apart. Per cell in mOhm, with the fit's Pearson r."""
    import numpy as np
    t = np.asarray(t, dtype=np.float64)
    volt = np.asarray(volt, dtype=np.float64)
    cur = np.asarray(cur, dtype=np.float64)
    dt = np.diff(t) / 1e6
    dv, di = np.diff(volt), np.diff(cur)
    k = ((dt > 0) & (dt <= MAX_GAP_S) & np.isfinite(dv) & np.isfinite(di)
         & (volt[1:] > 0) & (volt[:-1] > 0))          # voltage_v is 0 when invalid
    dv, di = dv[k], di[k]
    out = {"pairs": int(len(dv)), "range_a": None, "r": None, "mohm_per_cell": None}
    if len(cur):
        p5, p95 = (float(x) for x in np.percentile(cur, [5, 95]))
        out["range_a"] = round(p95 - p5, 3)
    if len(dv) < 3 or float(np.sum(di * di)) == 0.0 or float(np.std(dv)) == 0.0 or not n_cells:
        return out
    r_pack = -float(np.sum(dv * di)) / float(np.sum(di * di))
    out["r"] = round(float(np.corrcoef(dv, di)[0, 1]), 4)
    out["mohm_per_cell"] = round(r_pack / n_cells * 1000.0, 3)
    return out


def step_valid(s):
    return (s.get("mohm_per_cell") is not None and s["pairs"] >= DATA_MIN_PAIRS
            and (s.get("range_a") or 0) >= DATA_MIN_RANGE_A and s.get("r") is not None and s["r"] <= DATA_MAX_R)


def cells_of(b, mask, params):
    import numpy as np
    if "cell_count" in b:
        c = np.asarray(b["cell_count"], dtype=np.float64)[mask]
        c = c[np.isfinite(c) & (c > 0)]
        if len(c):
            return int(np.median(c)), "battery_status"
    for k in ("BAT1_N_CELLS", "BAT_N_CELLS"):
        try:
            v = int(params.get(k) or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            return v, k
    return None, None


def measure_data(data, params=None):
    """What this census records about one log, from {topic: {multi_id: {field: array}}} and the
    log's initial parameters (no positions)."""
    import numpy as np
    params = params or {}
    bats = data.get("battery_status") or {}
    out = {"battery_instances": len(bats),
           "active_instances": [int(m) for m in sorted(bats) if estimator_active(bats[m])]}
    if not bats:
        out["read"] = "no battery_status data"
        return out
    out["read"] = "ok"
    mid = pick_battery(bats)
    if mid is None:
        out["measurable"] = False
        out["why_not"] = "no active estimator with valid current"
        return out
    b = bats[mid]
    out["battery_instance"] = int(mid)
    t = np.asarray(b["timestamp"], dtype=np.int64)
    ld = (data.get("vehicle_land_detected") or {}).get(0)
    st = (data.get("vehicle_status") or {}).get(0)
    land = (ld["timestamp"], ld["landed"]) if ld and "landed" in ld else None
    status = (st["timestamp"], st["arming_state"]) if st and "arming_state" in st else None
    mask, source = airborne_mask(t, land, status)
    out["airborne_from"] = source
    if mask is None:
        out["measurable"] = False
        out["why_not"] = "no flight state"
        return out
    est = np.asarray(b[FIELD], dtype=np.float64)
    cur = np.asarray(b["current_a"], dtype=np.float64)
    volt = np.asarray(b["voltage_v"], dtype=np.float64) if "voltage_v" in b else None
    good = mask & np.isfinite(est) & np.isfinite(cur) & (cur >= 0)   # current_a is -1 when invalid
    n = int(np.sum(good))
    out["samples"] = n
    dt = float(np.median(np.diff(t))) / 1e6 if len(t) > 1 else 0.0
    out["airborne_s"] = round(n * dt, 1)
    if n < MIN_SAMPLES:
        out["measurable"] = False
        out["why_not"] = "fewer than %d airborne samples" % MIN_SAMPLES
        return out
    v = est[good]
    c = cur[good]
    out["current_median_a"] = round(float(np.median(c)), 3)
    if out["current_median_a"] < MIN_CURRENT_A:
        out["measurable"] = False
        out["why_not"] = "median airborne current under %.1f A" % MIN_CURRENT_A
        return out
    out["measurable"] = True
    zero = v <= 0.0
    tt = t[good]
    out["r_start_mohm"] = round(float(v[0]) * 1000.0, 4)
    out["r_end_mohm"] = round(float(v[-1]) * 1000.0, 4)
    out["r_median_mohm"] = round(float(np.median(v)) * 1000.0, 4)
    out["zero_end"] = bool(zero[-1])
    out["zero_share"] = round(float(np.mean(zero)), 4)
    out["ever_zero"] = bool(np.any(zero))
    out["collapsed"] = bool(np.all(zero[n // 2:]))
    out["first_zero_s"] = (round(float(tt[int(np.argmax(zero))] - tt[0]) / 1e6, 1) if np.any(zero) else None)
    out["last_change_frac"] = round(last_change_frac(list(v)), 4)
    out["changes"] = int(np.sum(v[1:] != v[:-1]))
    out["froze_early"] = out["last_change_frac"] <= EARLY
    cov = np.asarray(b[COV], dtype=np.float64)[good]
    out["cov_start"] = float(cov[0]) if np.isfinite(cov[0]) else None
    out["cov_end"] = float(cov[-1]) if np.isfinite(cov[-1]) else None
    cells, cells_from = cells_of(b, mask, params)
    out["cells"], out["cells_from"] = cells, cells_from
    if volt is not None:
        out["step"] = step_fit(tt, volt[good], c, cells)
        out["step_valid"] = step_valid(out["step"])
    else:
        out["step_valid"] = False
    return out


# ---------------------------------------------------------------- the check

def r_internal(params):
    """BAT1_R_INTERNAL (or the older BAT_R_INTERNAL) from the log's parameters, or None."""
    for k in ("BAT1_R_INTERNAL", "BAT_R_INTERNAL"):
        if k in (params or {}):
            try:
                return float(params[k])
            except (TypeError, ValueError):
                return None
    return None


def verdict(m, rint=None):
    """(level, text) for one measurement. level: 'note', 'ok' or 'not checked'."""
    if m.get("read") != "ok":
        return "not checked", "Battery resistance estimate not checked: %s." % m.get("read")
    if not m.get("measurable"):
        return "not checked", "Battery resistance estimate not checked: %s." % m.get("why_not")
    if m.get("collapsed"):
        fz = m.get("first_zero_s")
        when = ("it was already 0 when the vehicle took off" if not fz
                else "it first reached 0 %.1f s after takeoff" % fz)
        if rint is not None and rint >= 0:
            return "ok", ("PX4's battery-resistance estimate was 0 for the whole second half of the flight (%s), "
                          "but BAT1_R_INTERNAL is set (%.1f mOhm per cell), so PX4 used that value, not the "
                          "estimate." % (when, rint * 1000.0))
        setting = ("BAT1_R_INTERNAL is -1 (use the estimate)" if rint is not None
                   else "BAT1_R_INTERNAL is not in the log (PX4's default, -1, uses the estimate)")
        return "note", ("PX4's battery-resistance estimate was 0 for the whole second half of the flight (%s) "
                        "while %s, so PX4 did not correct the voltage-based state of charge for the voltage "
                        "drop under load: it reads low under load and recovers after landing. On public PX4 "
                        "flights this happened on %d of %d that use the estimate. A wrong current sensor scale "
                        "or offset can also cause it. See PX4's battery estimation guide: %s"
                        % (when, setting, FLEET["collapsed_using_estimate"], FLEET["used_estimate"], DOCS))
    used = " BAT1_R_INTERNAL is set, so PX4 used that value instead." if (rint is not None and rint >= 0) else ""
    return "ok", ("PX4's battery-resistance estimate did not collapse: it ended the flight at %.1f mOhm per cell "
                  "(public PX4 flights: median %.1f, p10-p90 %.1f-%.1f).%s"
                  % (m["r_end_mohm"], FLEET["alive_p50"], FLEET["alive_p10"], FLEET["alive_p90"], used))


def read_log(path):
    """({topic: {multi_id: {field: array}}}, initial parameters) from one ULog."""
    from pyulog import ULog
    u = ULog(path, list(TOPICS), True)
    data = {}
    for d in u.data_list:
        data.setdefault(d.name, {})[d.multi_id] = d.data
    return data, dict(u.initial_parameters)


def check(path):
    """The battery check of one ULog: measurement, level and text."""
    data, params = read_log(path)
    m = measure_data(data, params)
    rint = r_internal(params)
    level, text = verdict(m, rint)
    return {"battery_r": __version__, "level": level, "text": text,
            "collapsed": m.get("collapsed") if m.get("measurable") else None,
            "r_end_mohm": m.get("r_end_mohm") if m.get("measurable") else None,
            "bat_r_internal": rint, "measure": m}


def _plain(o):
    if hasattr(o, "item"):
        return o.item()
    if hasattr(o, "tolist"):
        return o.tolist()
    return str(o)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    results = []
    for p in args.logs:
        try:
            r = check(p)
        except Exception as exc:
            r = {"battery_r": __version__, "level": "not checked",
                 "text": "Battery resistance estimate not checked: %s: %s" % (type(exc).__name__, str(exc)[:200])}
        r["log"] = p
        results.append(r)
        if not args.json:
            print("%s\n  [%s] %s" % (p, r["level"], r["text"]))
    if args.json:
        print(json.dumps(results, indent=1, default=_plain))
    return 0


if __name__ == "__main__":
    sys.exit(main())

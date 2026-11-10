"""battr_logs.py: does PX4's online battery internal-resistance estimate collapse to zero or freeze
in flight? A census of public PX4 flight logs (px4-log-health study battery-r-2026, pre-registered
in studies/battery-r-2026/PLAN.md).

Since PX4 PR #23205 (merged 2024-07-02), the battery library estimates each pack's internal
resistance online with recursive least squares (V = V_oc - R * I, forgetting factor 0.95) and,
when BATn_R_INTERNAL is -1 (the default), uses it to correct the voltage-based state of charge for
load. An update is accepted only when the covariance norm falls below the smallest norm so far,
and a negative estimate is published as zero. A PX4 forum report (discuss.px4.io topic 49724,
2026-11-07) saw the estimate reach zero and stay there under load on one vehicle. This pass
measures how often that happens, and how early the estimate stops changing, across public logs.

It reads Flight Review's public list once and keeps real logs (no simulation, no CI) of at least
120 s, dated 2025-01-01 or later, from v1.15 or newer or with no readable release, newest first,
one log per vehicle. For each it streams the log and reads only its definitions section. If
battery_status has the internal_resistance_estimate field, it downloads the rest (at most 100 MB)
to a temporary file and reads battery_status, vehicle_land_detected and vehicle_status. No
position is read or written out, and the file is deleted after it is read. Requests are 6 s
apart, and the pass stops at the first block.

Before any log is read, it checks that studies/battery-r-2026/PLAN.md on px4-log-health's main
matches the local copy (line endings normalised), waiting up to --wait-min minutes for it.

    python battr_logs.py --out-dir out [--plan PLAN.md] [--budget-min 300] [--max-heads 600]
        [--max-full 150] [--wait-min 60]

Needs gnss_logs.py 0.1.0 (px4-gps-lag studies/gnss-2026) next to it.
By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import time

import gnss_logs as gn

__version__ = "0.1.0"

PLAN_URL = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/PLAN.md"
USER_AGENT = ("px4-log-health-battery-r/%s (MV2 of Munim, Inc.; mv2@munimtech.com; "
              "research on public logs)" % __version__)
SINCE = "2025-01-01"
MIN_SERIES = (1, 15)
MIN_DURATION_S = 120
MAX_LOG_BYTES = 100 << 20
KEEP_PARAMS = ("BAT1_R_INTERNAL", "BAT1_N_CELLS", "BAT1_SOURCE", "BAT1_CAPACITY", "BAT1_V_EMPTY",
               "BAT1_V_CHARGED", "BAT1_V_LOAD_DROP", "BAT_R_INTERNAL", "BAT_N_CELLS", "BAT_SOURCE",
               "BAT2_R_INTERNAL", "BAT2_SOURCE", "BAT_AVRG_CURRENT")
TOPICS = ("battery_status", "vehicle_land_detected", "vehicle_status")
FIELD = "internal_resistance_estimate"
COV = "estimation_covariance_norm"

# one log's measurement
MIN_SAMPLES = 150           # 30 s of airborne samples at the default 5 Hz
MIN_CURRENT_A = 0.5
EARLY = 0.25                # "froze early": last change in the first quarter of airborne samples
MAX_GAP_S = 0.5
DATA_MIN_PAIRS = 150
DATA_MIN_RANGE_A = 5.0
DATA_MAX_R = -0.3           # the step fit counts only if dV and dI are this anti-correlated

# the plan's thresholds
R1_SHARE = 0.05
R2_SHARE = 0.5
R3_LO_MOHM, R3_HI_MOHM = 1.0, 20.0
R4_RHO = 0.5
R5_SHARE = 0.8
MIN_N = 20
R4_MIN_N = 15


# ---------------------------------------------------------------- the public list

def eligible(entry):
    """(kind, '') for a log this pass may read, else (None, why)."""
    if "SITL" in str(entry.get("sys_hw") or "").upper():
        return None, "simulation"
    if "simul" in str(entry.get("type") or "").lower():
        return None, "simulation"
    if str(entry.get("source") or "").upper() == "CI":
        return None, "CI"
    if not entry.get("log_id"):
        return None, "no log id"
    if str(entry.get("log_date") or "")[:10] < SINCE:
        return None, "before %s" % SINCE
    try:
        duration = float(entry.get("duration_s") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration < MIN_DURATION_S:
        return None, "shorter than %d s" % MIN_DURATION_S
    rs = gn.release_series(entry.get("ver_sw_release"))
    if rs is not None and rs[0] < MIN_SERIES:
        return None, "older than v1.15"
    return (rs[1] if rs else "unknown"), ""


def build(entries):
    """(candidates in the pass's order: newest first, then by log id; counts)."""
    counts = {"listed": 0, "eligible": 0, "excluded": {}, "by_kind": {}}
    cands = []
    for e in entries:
        counts["listed"] += 1
        kind, why = eligible(e)
        if kind is None:
            counts["excluded"][why] = counts["excluded"].get(why, 0) + 1
            continue
        counts["eligible"] += 1
        counts["by_kind"][kind] = counts["by_kind"].get(kind, 0) + 1
        cands.append({"log_id": str(e["log_id"]), "log_date": str(e.get("log_date") or "")[:10],
                      "vehicle": gn.vehicle_hash(e.get("vehicle_uuid")) or "log:" + str(e["log_id"]),
                      "mav_type": str(e.get("mav_type") or ""), "sys_hw": str(e.get("sys_hw") or ""),
                      "ver_sw_release": str(e.get("ver_sw_release") or ""),
                      "ver_sw": str(e.get("ver_sw") or "")[:12], "kind": kind,
                      "duration_s": int(float(e.get("duration_s") or 0))})
    cands.sort(key=lambda c: c["log_id"])
    cands.sort(key=lambda c: c["log_date"], reverse=True)
    return cands, counts


# ---------------------------------------------------------------- reading one log

def pick(params):
    return {k: params[k] for k in sorted(params) if k.startswith(KEEP_PARAMS)}


def wanted(formats):
    return FIELD in str(formats.get("battery_status") or "")


def fetch_one(log_id, path, opener=None, max_bytes=MAX_LOG_BYTES):
    """Streams one public log. Returns (info, path or None): the whole log is written to `path`
    only when battery_status has internal_resistance_estimate and the log is at most max_bytes."""
    import urllib.request
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(gn.URL % log_id, headers={"User-Agent": USER_AGENT})
    with opener(req, timeout=300) as resp:
        chunks = gn.chunks_of(gn.open_stream(resp))
        formats, params, raw, complete = gn.read_definitions(chunks)
        info = {"head_complete": complete, "has_battery": "battery_status" in formats,
                "has_estimate": wanted(formats), "params": pick(params)}
        if not (complete and wanted(formats)):
            return info, None
        total = len(raw)
        with open(path, "wb") as f:
            f.write(raw)
            for c in chunks:
                total += len(c)
                if total > max_bytes:
                    break
                f.write(c)
    if total > max_bytes:
        os.remove(path)
        info["too_big"] = True
        return info, None
    info["bytes"] = total
    return info, path


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


def measure_file(path):
    from pyulog import ULog
    u = ULog(path, list(TOPICS), True)
    data = {}
    for d in u.data_list:
        data.setdefault(d.name, {})[d.multi_id] = d.data
    return measure_data(data, dict(u.initial_parameters))


# ---------------------------------------------------------------- the pass

def run(cands, fetch_fn, measure_fn, write, tmp_path, max_heads=600, max_full=150, sleep=time.sleep,
        clock=time.time, budget_s=300 * 60, spacing=gn.SPACING_S):
    """Reads candidates in order, one per vehicle, until max_heads logs were requested, max_full
    logs were downloaded, the list or the budget ran out, or Flight Review blocked a request.
    write(rec) records every request."""
    import urllib.error
    t0 = clock()
    seen = set()
    state = {"requested": 0, "full": 0, "errors_in_a_row": 0, "stop": None}
    for c in cands:
        if c["vehicle"] in seen:
            continue
        if state["requested"] >= max_heads:
            state["stop"] = "max heads"
            break
        if state["full"] >= max_full:
            state["stop"] = "max full logs"
            break
        if clock() - t0 > budget_s:
            state["stop"] = "time budget"
            break
        if state["requested"]:
            sleep(spacing)
        state["requested"] += 1
        seen.add(c["vehicle"])
        rec = dict(c, order=state["requested"])
        try:
            info, path = fetch_fn(c["log_id"], tmp_path)
            rec.update(info)
            rec["status"] = "estimate field" if info.get("has_estimate") else "no estimate field"
            if info.get("too_big"):
                rec["status"] = "too big"
            if path:
                state["full"] += 1
                try:
                    rec["measure"] = measure_fn(path)
                except Exception as exc:
                    rec["measure"] = {"read": "error: %s: %s" % (type(exc).__name__, str(exc)[:200])}
                finally:
                    if os.path.exists(path):
                        os.remove(path)
            state["errors_in_a_row"] = 0
        except urllib.error.HTTPError as exc:
            rec["status"] = "http %d" % exc.code
            if exc.code in (403, 429, 444):
                state["stop"] = "blocked: http %d" % exc.code
        except ValueError as exc:
            rec["status"] = "error: %s" % str(exc)[:200]
        except Exception as exc:   # one bad log must not stop the pass; five in a row do
            rec["status"] = "error: %s: %s" % (type(exc).__name__, str(exc)[:200])
            state["errors_in_a_row"] += 1
            if state["errors_in_a_row"] >= 5:
                state["stop"] = "five errors in a row"
        write(rec)
        if state["stop"]:
            break
    return {"requested": state["requested"], "downloaded": state["full"],
            "stop": state["stop"] or "list exhausted"}


# ---------------------------------------------------------------- scoring

def ranks(xs):
    """1-based ranks, ties get their average rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 2:
        return None
    ra, rb = ranks(a), ranks(b)
    n = float(len(a))
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def r_internal(rec):
    p = rec.get("params") or {}
    for k in ("BAT1_R_INTERNAL", "BAT_R_INTERNAL"):
        if k in p:
            try:
                return float(p[k])
            except (TypeError, ValueError):
                return None
    return None


def summarise(recs):
    """The plan's predictions and the reported figures, from every record written."""
    downloaded = [r for r in recs if r.get("measure")]
    read = [r for r in downloaded if r["measure"].get("read") == "ok"]
    meas = [r for r in read if r["measure"].get("measurable")]
    m = [r["measure"] for r in meas]
    collapsed = [x for x in m if x["collapsed"]]
    alive = [x for x in m if not x["collapsed"]]
    pairs = [(x["r_end_mohm"], x["step"]["mohm_per_cell"]) for x in m
             if x["r_end_mohm"] > 0 and x.get("step_valid")]
    rho = spearman([a for a, _ in pairs], [b for _, b in pairs])
    rint = [r_internal(r) for r in meas]
    rint_known = [v for v in rint if v is not None]
    p = {}
    p["R1"] = (len(collapsed) / len(m) >= R1_SHARE) if len(m) >= MIN_N else None
    p["R2"] = (sum(1 for x in m if x["froze_early"]) / len(m) >= R2_SHARE) if len(m) >= MIN_N else None
    med_alive = statistics.median([x["r_end_mohm"] for x in alive]) if alive else None
    p["R3"] = (R3_LO_MOHM <= med_alive <= R3_HI_MOHM) if len(alive) >= MIN_N else None
    p["R4"] = (rho is not None and rho < R4_RHO) if len(pairs) >= R4_MIN_N else None
    p["R5"] = (sum(1 for v in rint_known if v < 0) / len(rint_known) >= R5_SHARE) if len(rint_known) >= MIN_N else None
    rep = {
        "requests": len(recs),
        "status_counts": _counts(r.get("status", "") if not str(r.get("status", "")).startswith("error")
                                 else "error" for r in recs),
        "downloaded": len(downloaded), "read_ok": len(read), "measurable": len(m),
        "read_errors": [r["measure"].get("read") for r in downloaded if r["measure"].get("read") != "ok"],
        "why_not": _counts(r["measure"].get("why_not") for r in read if not r["measure"].get("measurable")),
        "collapsed": len(collapsed),
        "collapsed_share": round(len(collapsed) / len(m), 4) if m else None,
        "zero_end": sum(1 for x in m if x["zero_end"]),
        "ever_zero": sum(1 for x in m if x["ever_zero"]),
        "froze_early": sum(1 for x in m if x["froze_early"]),
        "froze_early_share": round(sum(1 for x in m if x["froze_early"]) / len(m), 4) if m else None,
        "never_changed_in_air": sum(1 for x in m if x["changes"] == 0),
        "last_change_frac_quantiles": _quantiles([x["last_change_frac"] for x in m]),
        "r_end_mohm_alive_quantiles": _quantiles([x["r_end_mohm"] for x in alive]),
        "r_end_alive_median_mohm": round(med_alive, 4) if med_alive is not None else None,
        "r_end_at_default_5mohm": sum(1 for x in m if abs(x["r_end_mohm"] - 5.0) < 1e-3),
        "step_valid": sum(1 for x in m if x.get("step_valid")),
        "step_mohm_quantiles": _quantiles([x["step"]["mohm_per_cell"] for x in m if x.get("step_valid")]),
        "r4_pairs": len(pairs),
        "spearman_r_end_vs_step": round(rho, 4) if rho is not None else None,
        "step_in_collapsed": _quantiles([x["step"]["mohm_per_cell"] for x in collapsed if x.get("step_valid")]),
        "first_zero_s_quantiles": _quantiles([x["first_zero_s"] for x in m if x.get("first_zero_s") is not None]),
        "r_internal_counts": _counts(rint),
        "collapsed_by_r_internal": _counts(r_internal(r) for r in meas if r["measure"]["collapsed"]),
        "mav_types": _counts(r.get("mav_type") for r in meas),
        "releases": _counts(r.get("ver_sw_release") for r in meas),
        "airborne_from": _counts(x.get("airborne_from") for x in m),
        "collapsed_rows": [{"order": r.get("order"), "sys_hw": r.get("sys_hw"), "mav_type": r.get("mav_type"),
                            "release": r.get("ver_sw_release"), "r_internal": r_internal(r),
                            "first_zero_s": r["measure"].get("first_zero_s"),
                            "zero_share": r["measure"].get("zero_share"),
                            "step_mohm": (r["measure"].get("step") or {}).get("mohm_per_cell")
                            if r["measure"].get("step_valid") else None}
                           for r in meas if r["measure"]["collapsed"]],
    }
    return {"battr_logs": __version__, "predictions": p, "reported": rep}


def _counts(xs):
    out = {}
    for x in xs:
        k = str(x)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _quantiles(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    def q(p):
        return round(xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))], 4)
    return {"n": len(xs), "p10": q(0.10), "p25": q(0.25), "median": round(statistics.median(xs), 4),
            "p75": q(0.75), "p90": q(0.90), "max": xs[-1]}


def main(argv=None):
    import ijson
    import urllib.request
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--plan", default="PLAN.md")
    ap.add_argument("--budget-min", type=float, default=300)
    ap.add_argument("--max-heads", type=int, default=600)
    ap.add_argument("--max-full", type=int, default=150)
    ap.add_argument("--wait-min", type=float, default=60)
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)

    def get_plan():
        req = urllib.request.Request(PLAN_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    if not gn.wait_for_plan(args.plan, get_plan, args.wait_min * 60):
        print("PLAN.md is not on main: no log read", flush=True)
        with open(os.path.join(args.out_dir, "battr-summary.json"), "w") as f:
            json.dump({"battr_logs": __version__, "stop": "plan not on main; no log read"}, f, indent=1)
        return 3
    started = time.time()
    req = urllib.request.Request(gn.DBINFO, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as resp:
        cands, counts = build(ijson.items(gn.open_stream(resp), "item"))
    counts["candidates"] = len(cands)
    counts["candidate_vehicles"] = len({c["vehicle"] for c in cands})
    print(json.dumps(counts), flush=True)
    recs = []
    tmp = os.path.join(tempfile.gettempdir(), "battr-log.ulg")
    with open(os.path.join(args.out_dir, "battr-rows.jsonl"), "w", encoding="utf-8") as out:
        def write(rec):
            recs.append(rec)
            out.write(json.dumps(rec, default=gn.plain) + "\n")
            out.flush()
            m = rec.get("measure") or {}
            print("%d %s %s %s %s %s %s %s" % (
                rec["order"], rec["mav_type"], rec["log_id"], rec["ver_sw_release"], rec["status"],
                m.get("measurable"), m.get("r_end_mohm"), m.get("why_not")), flush=True)
        res = run(cands, fetch_one, measure_file, write, tmp, max_heads=args.max_heads,
                  max_full=args.max_full, budget_s=args.budget_min * 60)
    summary = summarise(recs)
    summary.update({"list": counts, "pass": res, "wall_s": round(time.time() - started, 1)})
    text = json.dumps(summary, indent=1, default=gn.plain)
    with open(os.path.join(args.out_dir, "battr-summary.json"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

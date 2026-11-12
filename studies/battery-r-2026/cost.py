"""cost.py: what a collapsed PX4 battery internal-resistance estimate does to the published battery
percentage (px4-log-health study battery-r-2026, cost, pre-registered in
studies/battery-r-2026/PLAN-cost.md).

Two modes:

    python cost.py analytic --out-dir out [--replay-rows replay-rows.jsonl]
        [--params-rows battr-rows.jsonl onset-rows.jsonl steps-rows.jsonl]
        The voltage arithmetic only, from the published rows (no log is read).

    python cost.py study --out-dir out [--plan PLAN-cost.md] [--replay-rows replay-rows.jsonl]
        [--budget-min 300] [--max-full 230] [--wait-min 30]
        Re-reads the replayed flights from Flight Review (6 s apart, stops at the first block),
        measures how battery_status.remaining follows the current, and grades P1-P3.

The study mode needs battr_logs.py 0.1.0 (px4-log-health) and gnss_logs.py 0.1.0 (px4-gps-lag)
next to it. No position is read or written.

0.1.1 (2026-11-12, before any log was read): non-finite values are kept out of the arithmetic.
R_used is the median of the finite logged estimates over the active samples (a NaN estimate is
not a logged value of R), and a k or k_exp that is not finite counts as not known, so it can
never enter the sets C and A or the rank correlation. The plan's rules are unchanged.

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
import time

__version__ = "0.1.1"

PLAN_URL = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/PLAN-cost.md"

# the plan's rules
ACTIVE_MARGIN = 0.01        # remaining at least this far below the coulomb bound
MIN_ACTIVE = 150
MIN_RANGE_A = 3.0
P1_RATIO, P1_MIN = 0.5, 6
P2_MIN_C, P2_MIN_A = 6, 20
P3_RHO, P3_MIN = 0.3, 26

CENSUS_KEYS = ("measurable", "why_not", "collapsed", "first_zero_s", "r_end_mohm", "r_start_mohm",
               "cells", "step", "step_valid", "battery_instance", "samples", "airborne_s",
               "current_median_a")


def finite(x):
    """True for a real, finite number (not None, not NaN, not inf)."""
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- parameters

def param(params, name, inst):
    """BAT<inst+1>_<name>, else BAT1_<name>, else BAT_<name>; None if absent or not a number."""
    params = params or {}
    for k in ("BAT%d_%s" % (int(inst or 0) + 1, name), "BAT1_" + name, "BAT_" + name):
        if k in params:
            try:
                v = float(params[k])
            except (TypeError, ValueError):
                return None
            return v if math.isfinite(v) else None
    return None


def span_of(params, inst):
    """V_CHARGED - V_EMPTY per cell, or None if unknown or not positive."""
    hi = param(params, "V_CHARGED", inst)
    lo = param(params, "V_EMPTY", inst)
    if hi is None or lo is None or hi <= lo:
        return None
    return hi - lo


def r_used_of(params, inst, estimate_ohm):
    """The resistance PX4 compensates with: BATn_R_INTERNAL if >= 0, else the estimate."""
    r = param(params, "R_INTERNAL", inst)
    if r is not None and r >= 0:
        return r
    return estimate_ohm if finite(estimate_ohm) else None


def k_expected(step_mohm, r_used_ohm, span):
    """-(R_step - R_used) / span, per ampere (fraction of full scale); None if not finite."""
    if not finite(step_mohm) or not finite(r_used_ohm) or not finite(span) or not span:
        return None
    v = -(step_mohm / 1000.0 - r_used_ohm) / span
    return v if math.isfinite(v) else None


# ---------------------------------------------------------------- one flight

def active_mask(good, remaining, discharged, capacity):
    """Samples where the voltage branch is the lower one (see the plan)."""
    import numpy as np
    good = np.asarray(good, dtype=bool)
    rem = np.asarray(remaining, dtype=np.float64)
    if capacity is not None and capacity > 0 and discharged is not None:
        dis = np.asarray(discharged, dtype=np.float64)
        bound = np.maximum(1.0 - dis / capacity, 0.0)
        with np.errstate(invalid="ignore"):
            return good & np.isfinite(dis) & (rem <= bound - ACTIVE_MARGIN)
    return good.copy()


def fit_k(t_us, remaining, current):
    """OLS of remaining on [1, t, t^2, I] (t standardised). Returns (k per A, residual sd)."""
    import numpy as np
    t = np.asarray(t_us, dtype=np.float64) / 1e6
    y = np.asarray(remaining, dtype=np.float64)
    i = np.asarray(current, dtype=np.float64)
    sd = float(np.std(t))
    z = (t - float(np.mean(t))) / (sd if sd > 0 else 1.0)
    x = np.column_stack([np.ones_like(z), z, z * z, i])
    coef, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
    res = y - x.dot(coef)
    return float(coef[3]), float(np.std(res))


def median_finite(values, mask):
    """Median of the finite values under mask, or None if there are none."""
    import numpy as np
    v = np.asarray(values, dtype=np.float64)
    m = np.asarray(mask, dtype=bool) & np.isfinite(v)
    return float(np.median(v[m])) if np.any(m) else None


def measure_cost(data, params=None):
    """The census measurement (battr_logs 0.1.0, unchanged) plus k and k_exp, from
    {topic: {multi_id: {field: array}}} and the log's initial parameters."""
    import numpy as np
    import battr_logs as bl
    params = params or {}
    census = bl.measure_data(data, params)
    out = {"read": census.get("read"),
           "census": {k: census[k] for k in CENSUS_KEYS if k in census}}
    if not census.get("measurable"):
        out["measured"] = False
        out["why_not"] = census.get("why_not") or census.get("read")
        return out
    inst = census["battery_instance"]
    b = data["battery_status"][inst]
    if "remaining" not in b:
        out["measured"] = False
        out["why_not"] = "no remaining field"
        return out
    t = np.asarray(b["timestamp"], dtype=np.int64)
    cur = np.asarray(b["current_a"], dtype=np.float64)
    rem = np.asarray(b["remaining"], dtype=np.float64)
    est = np.asarray(b[bl.FIELD], dtype=np.float64)
    dis = np.asarray(b["discharged_mah"], dtype=np.float64) if "discharged_mah" in b else None
    ld = (data.get("vehicle_land_detected") or {}).get(0)
    st = (data.get("vehicle_status") or {}).get(0)
    land = (ld["timestamp"], ld["landed"]) if ld and "landed" in ld else None
    status = (st["timestamp"], st["arming_state"]) if st and "arming_state" in st else None
    mask, _ = bl.airborne_mask(t, land, status)
    with np.errstate(invalid="ignore"):
        good = mask & np.isfinite(rem) & (rem >= 0) & np.isfinite(cur) & (cur >= 0)
    capacity = param(params, "CAPACITY", inst)
    act = active_mask(good, rem, dis, capacity)
    n_good, n_act = int(np.sum(good)), int(np.sum(act))
    out["airborne"] = n_good
    out["active"] = n_act
    out["active_share"] = round(n_act / float(n_good), 4) if n_good else None
    out["capacity_mah"] = capacity
    out["estimate_nonfinite_active"] = int(np.sum(act & ~np.isfinite(est)))
    span = span_of(params, inst)
    out["span_v"] = round(span, 4) if span else None
    r_est = median_finite(est, act)
    r_used = r_used_of(params, inst, r_est)
    out["r_used_mohm"] = round(r_used * 1000.0, 4) if r_used is not None else None
    step = census.get("step") or {}
    step_mohm = step.get("mohm_per_cell") if census.get("step_valid") else None
    out["k_exp"] = k_expected(step_mohm, r_used, span)
    if n_act < MIN_ACTIVE:
        out["measured"] = False
        out["why_not"] = "fewer than %d active samples" % MIN_ACTIVE
        return out
    ca = cur[act]
    p5, p95 = (float(x) for x in np.percentile(ca, [5, 95]))
    out["active_range_a"] = round(p95 - p5, 3)
    out["active_current_median_a"] = round(float(np.median(ca)), 3)
    if p95 - p5 < MIN_RANGE_A:
        out["measured"] = False
        out["why_not"] = "active current range under %.0f A" % MIN_RANGE_A
        return out
    k, sd = fit_k(t[act], rem[act], ca)
    if not math.isfinite(k):
        out["measured"] = False
        out["why_not"] = "fit not finite"
        return out
    out["measured"] = True
    out["k"] = k
    out["residual_sd"] = round(sd, 6)
    return out


def measure_file(path):
    from pyulog import ULog
    import battr_logs as bl
    u = ULog(path, list(bl.TOPICS), True)
    data = {}
    for d in u.data_list:
        data.setdefault(d.name, {})[d.multi_id] = d.data
    return measure_cost(data, dict(u.initial_parameters))


# ---------------------------------------------------------------- grading

def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _quantiles(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None

    def q(p):
        return round(xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))], 4)
    return {"n": len(xs), "p10": q(0.10), "p25": q(0.25), "median": round(statistics.median(xs), 4),
            "p75": q(0.75), "p90": q(0.90), "max": round(xs[-1], 4)}


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 2:
        return None
    ra, rb = _ranks(a), _ranks(b)
    n = float(len(a))
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def grade(measures):
    """(predictions, report) with the plan's rules, from per-flight measure dicts."""
    ms = [m for m in measures if m.get("measured") and finite(m.get("k"))]
    col = [m for m in ms if (m.get("census") or {}).get("collapsed")]
    alive = [m for m in ms if not (m.get("census") or {}).get("collapsed")]
    c_set = [m for m in col if finite(m.get("k_exp")) and m["k_exp"] < 0]
    a_set = [m for m in alive if finite(m.get("k_exp"))]
    ratios = [m["k"] / m["k_exp"] for m in c_set]
    med_ratio = _median(ratios)
    med_kc = _median([m["k"] for m in c_set])
    med_ka = _median([m["k"] for m in a_set])
    both = c_set + a_set
    rho = spearman([m["k"] for m in both], [m["k_exp"] for m in both])
    p = {
        "P1": (med_ratio >= P1_RATIO) if len(c_set) >= P1_MIN else None,
        "P2": (med_kc < med_ka) if (len(c_set) >= P2_MIN_C and len(a_set) >= P2_MIN_A) else None,
        "P3": (rho is not None and rho >= P3_RHO) if len(both) >= P3_MIN else None,
    }

    def pp10(xs):
        return _quantiles([x * 1000.0 for x in xs])     # fraction per A -> pp per 10 A

    def under(m, key):
        i = m.get("active_current_median_a")
        return None if i is None or m.get(key) is None else -m[key] * i * 100.0

    rep = {
        "measured": len(ms), "collapsed_measured": len(col), "alive_measured": len(alive),
        "C": len(c_set), "A": len(a_set),
        "p1_median_k_over_k_exp": round(med_ratio, 4) if med_ratio is not None else None,
        "p2_median_k_C": med_kc, "p2_median_k_A": med_ka,
        "p3_pairs": len(both), "p3_rho": round(rho, 4) if rho is not None else None,
        "k_pp_per_10A_C": pp10([m["k"] for m in c_set]),
        "k_exp_pp_per_10A_C": pp10([m["k_exp"] for m in c_set]),
        "k_pp_per_10A_A": pp10([m["k"] for m in a_set]),
        "k_exp_pp_per_10A_A": pp10([m["k_exp"] for m in a_set]),
        "under_read_pp_at_median_current_C": _quantiles([under(m, "k") for m in c_set]),
        "expected_under_read_pp_C": _quantiles([under(m, "k_exp") for m in c_set]),
        "under_read_pp_at_median_current_A": _quantiles([under(m, "k") for m in a_set]),
        "active_share": _quantiles([m.get("active_share") for m in ms]),
        "active_share_unmeasured": _quantiles([m.get("active_share") for m in measures
                                               if not m.get("measured")]),
        "k_exp_not_known": {"collapsed": sum(1 for m in col if not finite(m.get("k_exp"))),
                            "alive": sum(1 for m in alive if not finite(m.get("k_exp")))},
        "C_rows": [{"k_pp_per_10A": round(m["k"] * 1000.0, 3),
                    "k_exp_pp_per_10A": round(m["k_exp"] * 1000.0, 3),
                    "step_mohm": ((m.get("census") or {}).get("step") or {}).get("mohm_per_cell"),
                    "r_used_mohm": m.get("r_used_mohm"), "span_v": m.get("span_v"),
                    "active_current_median_a": m.get("active_current_median_a"),
                    "active_share": m.get("active_share")} for m in c_set],
    }
    return p, rep


# ---------------------------------------------------------------- the analytic calculation

def rows_of(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def params_by_log(rows_lists):
    """{log_id: params} from any earlier row that recorded the log's parameters."""
    out = {}
    for rows in rows_lists:
        for r in rows:
            p = r.get("params")
            if r.get("log_id") and isinstance(p, dict) and p and r["log_id"] not in out:
                out[str(r["log_id"])] = p
    return out


def analytic(replay_rows, params):
    """Voltage arithmetic from the published rows: (R_step - R_used) x median airborne current /
    span, in percentage points, for replayed flights with a valid step fit and known span."""
    rows, skipped = [], {}

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1

    for r in replay_rows:
        m = r.get("measure") or {}
        if not m.get("replayed"):
            continue
        c = m.get("census") or {}
        if not c.get("step_valid"):
            skip("no valid step fit")
            continue
        p = params.get(str(r.get("log_id")))
        if not p:
            skip("no parameters in the rows")
            continue
        inst = c.get("battery_instance", 0)
        span = span_of(p, inst)
        if not span:
            skip("no V_CHARGED > V_EMPTY")
            continue
        r_end = c.get("r_end_mohm")
        r_used = r_used_of(p, inst, None if not finite(r_end) else r_end / 1000.0)
        step = (c.get("step") or {}).get("mohm_per_cell")
        i = c.get("current_median_a")
        if r_used is None or not finite(step) or not finite(i):
            skip("missing value")
            continue
        rows.append({"collapsed": bool(c.get("collapsed")), "step_mohm": step,
                     "r_used_mohm": round(r_used * 1000.0, 4), "current_median_a": i,
                     "span_v": round(span, 4),
                     "uncompensated_pp": round((step / 1000.0 - r_used) * i / span * 100.0, 3),
                     "full_sag_pp": round(step / 1000.0 * i / span * 100.0, 3)})
    col = [x for x in rows if x["collapsed"]]
    alive = [x for x in rows if not x["collapsed"]]
    return {"cost": __version__, "flights": len(rows), "collapsed": len(col), "alive": len(alive),
            "skipped": skipped,
            "uncompensated_pp_collapsed": _quantiles([x["uncompensated_pp"] for x in col]),
            "uncompensated_pp_alive": _quantiles([x["uncompensated_pp"] for x in alive]),
            "full_sag_pp_all": _quantiles([x["full_sag_pp"] for x in rows]),
            "current_median_a_collapsed": _quantiles([x["current_median_a"] for x in col]),
            "span_v": _quantiles([x["span_v"] for x in rows]),
            "collapsed_rows": col}


# ---------------------------------------------------------------- the study pass

def load_targets(replay_rows):
    """Replayed flights, collapsed first, then in replay order; one entry per log id."""
    out, seen = [], set()
    for r in replay_rows:
        m = r.get("measure") or {}
        lid = r.get("log_id")
        if not m.get("replayed") or not lid or lid in seen:
            continue
        seen.add(lid)
        out.append({"log_id": lid, "pass": r.get("pass"), "replay_order": r.get("order"),
                    "vehicle": r.get("vehicle"), "sys_hw": r.get("sys_hw"),
                    "mav_type": r.get("mav_type"), "ver_sw_release": r.get("ver_sw_release"),
                    "row_collapsed": bool((m.get("census") or {}).get("collapsed"))})
    out.sort(key=lambda x: (not x["row_collapsed"], x["replay_order"] or 0))
    return out


def run(targets, fetch_fn, measure_fn, write, tmp_path, max_full=230, sleep=time.sleep,
        clock=time.time, budget_s=300 * 60, spacing=6.0):
    """Re-reads the targets in order until max_full logs were read, the list or the budget ran
    out, Flight Review blocked a request, or five requests in a row failed."""
    import urllib.error
    t0 = clock()
    state = {"requested": 0, "full": 0, "errors": 0, "stop": None}
    for c in targets:
        if state["full"] >= max_full:
            state["stop"] = "max full logs"
            break
        if clock() - t0 > budget_s:
            state["stop"] = "time budget"
            break
        if state["requested"]:
            sleep(spacing)
        state["requested"] += 1
        rec = dict(c, order=state["requested"])
        try:
            info, path = fetch_fn(c["log_id"], tmp_path)
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
            state["errors"] = 0
        except urllib.error.HTTPError as exc:
            rec["status"] = "http %d" % exc.code
            if exc.code in (403, 429, 444):
                state["stop"] = "blocked: http %d" % exc.code
        except Exception as exc:
            rec["status"] = "error: %s: %s" % (type(exc).__name__, str(exc)[:200])
            state["errors"] += 1
            if state["errors"] >= 5:
                state["stop"] = "five errors in a row"
        write(rec)
        if state["stop"]:
            break
    return {"requested": state["requested"], "downloaded": state["full"],
            "stop": state["stop"] or "list exhausted"}


def _counts(xs):
    out = {}
    for x in xs:
        k = str(x)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _plain(o):
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    return str(o)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=("analytic", "study"))
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--plan", default="PLAN-cost.md")
    ap.add_argument("--replay-rows", default="replay-rows.jsonl")
    ap.add_argument("--params-rows", nargs="*",
                    default=["battr-rows.jsonl", "onset-rows.jsonl", "steps-rows.jsonl"])
    ap.add_argument("--budget-min", type=float, default=300)
    ap.add_argument("--max-full", type=int, default=230)
    ap.add_argument("--wait-min", type=float, default=30)
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)
    replay_rows = rows_of(args.replay_rows)

    if args.mode == "analytic":
        params = params_by_log([rows_of(p) for p in args.params_rows if os.path.exists(p)])
        res = analytic(replay_rows, params)
        res["params_logs"] = len(params)
        text = json.dumps(res, indent=1, default=_plain)
        with open(os.path.join(args.out_dir, "analytic-summary.json"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(text, flush=True)
        return 0

    import urllib.request
    import battr_logs as bl
    import gnss_logs as gn

    def get_plan():
        req = urllib.request.Request(PLAN_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    if not gn.wait_for_plan(args.plan, get_plan, args.wait_min * 60):
        print("PLAN-cost.md is not on main: no log read", flush=True)
        with open(os.path.join(args.out_dir, "cost-summary.json"), "w") as f:
            json.dump({"cost": __version__, "stop": "plan not on main; no log read"}, f, indent=1)
        return 3
    targets = load_targets(replay_rows)
    n_col = sum(1 for x in targets if x["row_collapsed"])
    assert len(targets) >= 200 and n_col >= 20, "replay rows look wrong: %d flights, %d collapsed" % (
        len(targets), n_col)
    print(json.dumps({"targets": len(targets), "collapsed": n_col}), flush=True)
    started = time.time()
    recs = []
    tmp = os.path.join(tempfile.gettempdir(), "cost-log.ulg")
    with open(os.path.join(args.out_dir, "cost-rows.jsonl"), "w", encoding="utf-8") as out:
        def write(rec):
            recs.append(rec)
            out.write(json.dumps(rec, default=_plain) + "\n")
            out.flush()
            m = rec.get("measure") or {}
            print("%d %s %s collapsed=%s measured=%s k=%s k_exp=%s %s" % (
                rec["order"], rec["log_id"], rec["status"], rec["row_collapsed"], m.get("measured"),
                m.get("k"), m.get("k_exp"), m.get("why_not") or ""), flush=True)
        res = run(targets, bl.fetch_one, measure_file, write, tmp, max_full=args.max_full,
                  budget_s=args.budget_min * 60, spacing=gn.SPACING_S)
    pred, rep = grade([r["measure"] for r in recs if r.get("measure")])
    summary = {"cost": __version__, "battr_logs": bl.__version__, "predictions": pred, "reported": rep,
               "status_counts": _counts(r.get("status") for r in recs),
               "why_not": _counts((r.get("measure") or {}).get("why_not") for r in recs
                                  if r.get("measure") and not r["measure"].get("measured")),
               "targets": len(targets), "targets_collapsed": n_col, "pass": res,
               "wall_s": round(time.time() - started, 1)}
    text = json.dumps(summary, indent=1, default=_plain)
    with open(os.path.join(args.out_dir, "cost-summary.json"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

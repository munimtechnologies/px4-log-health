"""replay.py: replay PX4's battery internal-resistance estimator offline on the logged voltage and
current of public flights, and check what the logged covariance norm says about its update gate
(px4-log-health study battery-r-2026, replay, pre-registered in
studies/battery-r-2026/PLAN-replay.md).

PX4's battery library (src/lib/battery/battery.cpp, Battery::updateInternalResistanceEstimation)
fits V = OCV - R * I by recursive least squares with forgetting factor LAMBDA = 0.95 and accepts an
update only if the Frobenius norm of the new covariance is smaller than the stored norm; a rejected
update only resets the OCV estimate to V + R * I. It publishes max(R / n_cells, 0). This file ports
that update line for line, in float32, and runs it over the samples of battery_status that the
log holds (about 5 Hz), as if the battery had been connected when the log started (PX4 resets the
estimator on every sample during the first 2 s after connection). It also runs the same least
squares without the gate (float64), as a reference.

It re-reads only flights that the three earlier passes (battr-rows.jsonl, onset-rows.jsonl,
steps-rows.jsonl) already found measurable: collapsed flights first, then the others, by pass and
order. Each log is streamed from Flight Review, read for battery_status, vehicle_land_detected and
vehicle_status only (no position), and deleted. Requests are 6 s apart, and the pass stops at the
first block.

Before any log is read, it checks that studies/battery-r-2026/PLAN-replay.md on px4-log-health's
main matches the local copy (line endings normalised), waiting up to --wait-min minutes for it.

    python replay.py --out-dir out [--plan PLAN-replay.md] [--first-pass first-pass-rows.jsonl]
        [--onset-pass onset-rows.jsonl] [--steps-pass steps-rows.jsonl] [--budget-min 300]
        [--max-full 230] [--wait-min 60]

Needs battr_logs.py 0.1.0 (px4-log-health) and gnss_logs.py 0.1.0 (px4-gps-lag) next to it.
By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import argparse
import json
import math
import os
import sys
import tempfile
import time

__version__ = "0.1.0"

PLAN_URL = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/PLAN-replay.md"

# PX4's constants (src/lib/battery/battery.h and battery.cpp on main, read 2026-11-10)
LAMBDA = 0.95
R_DEFAULT = 0.005           # Ohm per cell
OCV_DEFAULT = 4.2           # V per cell
OCV_COVARIANCE = 1.5        # per cell
R_COVARIANCE = 0.1          # per cell
RECOGNITION_V = 2.1         # LITHIUM_BATTERY_RECOGNITION_VOLTAGE (pack voltage)
INIT_US = 2000000           # filters (and the estimator) are reset for 2 s after connection
FLT_EPSILON = 1.1920929e-07

# derived: the gated norm can never fall to 1 - LAMBDA (see PLAN-replay.md)
NORM_FLOOR = 1.0 - LAMBDA
FLOOR_TOL = 0.0499
RESET_WINDOW_US = 3000000   # an increase within 3 s of a logged disconnection is a reset

# the plan's thresholds
R_FLAT = -0.1
C1_SHARE, C2_SHARE, C_MIN = 0.95, 0.99, 50
P1_SHARE, P1_MIN = 0.85, 30
P2_SHARE, P2_MIN = 0.60, 6
P3_SHARE, P3_MIN = 0.60, 6
P4_RHO, P4_MIN = 0.5, 20
P5_SHARE, P5_MIN = 0.60, 6

PASSES = ("first", "onset", "steps")
CENSUS_KEYS = ("measurable", "why_not", "collapsed", "first_zero_s", "r_end_mohm", "r_start_mohm",
               "cells", "cells_from", "step", "step_valid", "battery_instance", "samples",
               "airborne_s", "changes", "froze_early", "cov_start", "cov_end", "current_median_a")


# ---------------------------------------------------------------- PX4's estimator, ported

class Rls(object):
    """Battery's internal-resistance RLS. gated=True is PX4's code; gated=False accepts every
    update (a plain RLS with the same forgetting factor). dtype float32 by default, as on PX4."""

    def __init__(self, n_cells, gated=True, dtype=None):
        import numpy as np
        self.f = dtype or np.float32
        f = self.f
        self.gated = gated
        self.n = f(n_cells)
        self.lam = f(LAMBDA)
        # Battery::updateParams() sets this before any reset
        self.e0 = f(OCV_DEFAULT) * self.n
        self.e1 = f(R_DEFAULT) * self.n
        self._set_p0()
        self.r_pub = f(R_DEFAULT)
        self.accepted = 0
        self.rejected = 0

    def _set_p0(self):
        f = self.f
        self.p00 = f(OCV_COVARIANCE) * self.n
        self.p01 = f(0.0)
        self.p10 = f(0.0)
        self.p11 = f(R_COVARIANCE) * self.n
        self.norm = self._norm(self.p00, self.p10, self.p11)

    def _norm(self, a, c, d):
        import numpy as np
        two = self.f(2.0)
        return np.sqrt(a * a + two * c * c + d * d)

    def reset(self, voltage_v, current_a):
        """Battery::resetInternalResistanceEstimation()."""
        f = self.f
        self.e0 = f(voltage_v)
        self.e1 = f(R_DEFAULT) * self.n
        self._set_p0()
        self.r_pub = f(R_DEFAULT)

    def update(self, voltage_v, current_a):
        """Battery::updateInternalResistanceEstimation(). True if the update was accepted."""
        import numpy as np
        with np.errstate(all="ignore"):     # the ungated reference may wind up; never PX4's path
            return self._update(voltage_v, current_a)

    def _update(self, voltage_v, current_a):
        import numpy as np
        f = self.f
        v = f(voltage_v)
        i = f(current_a)
        x0 = f(1.0)
        x1 = -i
        vp = x0 * self.e0 + x1 * self.e1
        pe = v - vp
        px0 = self.p00 * x0 + self.p01 * x1
        px1 = self.p10 * x0 + self.p11 * x1
        den = self.lam + (x0 * px0 + x1 * px1)
        g0 = px0 / den
        g1 = px1 / den
        t0 = self.e0 + g0 * pe
        t1 = self.e1 + g1 * pe
        xp0 = x0 * self.p00 + x1 * self.p10
        xp1 = x0 * self.p01 + x1 * self.p11
        q00 = (self.p00 - g0 * xp0) / self.lam
        q01 = (self.p01 - g0 * xp1) / self.lam
        q10 = (self.p10 - g1 * xp0) / self.lam
        q11 = (self.p11 - g1 * xp1) / self.lam
        qn = self._norm(q00, q10, q11)
        if (not self.gated) or qn < self.norm:
            self.e0, self.e1 = t0, t1
            self.p00, self.p01, self.p10, self.p11 = q00, q01, q10, q11
            self.norm = qn
            self.r_pub = max(self.e1 / self.n, f(0.0))
            self.accepted += 1
            return True
        self.e0 = v + self.e1 * i
        self.rejected += 1
        return False


def replay(t, volt, cur, conn, n_cells, gated=True, dtype=None):
    """Runs the estimator over logged samples (t in us), as if the battery connected at the first
    sample. Returns arrays aligned with the input: r_pub (published, Ohm per cell), r_raw (R / n,
    may be negative), norm, accepted (bool), updated (bool: current > FLT_EPSILON)."""
    import numpy as np
    rls = Rls(n_cells, gated, dtype)
    n = len(t)
    out = {"r_pub": np.full(n, np.nan), "r_raw": np.full(n, np.nan), "norm": np.full(n, np.nan),
           "accepted": np.zeros(n, dtype=bool), "updated": np.zeros(n, dtype=bool)}
    last_unconnected = None
    for k in range(n):
        v = float(volt[k])
        i = float(cur[k])
        if not math.isfinite(v):
            continue
        connected = bool(conn[k]) and v >= RECOGNITION_V
        tk = int(t[k])
        if last_unconnected is None or not connected:
            last_unconnected = tk
        initialized = connected and tk > last_unconnected + INIT_US
        if connected and not initialized:
            rls.reset(v, i if math.isfinite(i) else -1.0)
        if math.isfinite(i) and i > FLT_EPSILON:
            out["updated"][k] = True
            out["accepted"][k] = rls.update(v, i)
        out["r_pub"][k] = float(rls.r_pub)
        out["r_raw"][k] = float(rls.e1 / rls.n)
        out["norm"][k] = float(rls.norm)
    return out


# ---------------------------------------------------------------- the logged norm

def norm_check(t, cov, volt, conn, cell_count, n_cells, mask=None):
    """What the logged estimation_covariance_norm says: increases between consecutive finite
    samples, and whether a reset explains each (a logged disconnection or voltage under 2.1 V in
    the 3 s before, a cell-count change, a value within 1 % of the reset norm, or a previous value
    of 0). Plus the smallest positive norm and, within `mask` (airborne), how often it fell."""
    import numpy as np
    t = np.asarray(t, dtype=np.int64)
    cov = np.asarray(cov, dtype=np.float64)
    volt = np.asarray(volt, dtype=np.float64)
    conn = np.asarray(conn).astype(bool)
    bad = (~conn) | ~np.isfinite(volt) | (volt < RECOGNITION_V)
    disc = t[bad]
    reset_norm = n_cells * math.sqrt(OCV_COVARIANCE ** 2 + R_COVARIANCE ** 2) if n_cells else None
    idx = np.flatnonzero(np.isfinite(cov))
    inc = unexplained = air_pairs = air_dec = 0
    max_rel = 0.0
    for a, b in zip(idx[:-1], idx[1:]):
        if mask is not None and mask[a] and mask[b]:
            air_pairs += 1
            if cov[b] < cov[a]:
                air_dec += 1
        if not cov[b] > cov[a]:
            continue
        inc += 1
        explained = cov[a] <= 0.0
        j = int(np.searchsorted(disc, t[b], side="right"))
        if j > 0 and t[b] - disc[j - 1] <= RESET_WINDOW_US:
            explained = True
        if cell_count is not None and cell_count[a] != cell_count[b]:
            explained = True
        if reset_norm and abs(cov[b] - reset_norm) <= 0.01 * reset_norm:
            explained = True
        if not explained:
            unexplained += 1
            max_rel = max(max_rel, float((cov[b] - cov[a]) / cov[a]))
    pos = cov[idx]
    pos = pos[pos > 0]
    return {"pairs": max(0, len(idx) - 1), "increases": inc, "unexplained": unexplained,
            "max_unexplained_rel": round(max_rel, 6),
            "nonpositive": int(len(idx) - len(pos)),
            "min_norm": float(np.min(pos)) if len(pos) else None,
            "start_norm": float(pos[0]) if len(pos) else None,
            "end_norm": float(pos[-1]) if len(pos) else None,
            "air_pairs": air_pairs, "air_decreases": air_dec,
            "air_accept_share": round(air_dec / air_pairs, 4) if air_pairs else None}


# ---------------------------------------------------------------- one log

def measure_replay(data, params=None):
    """The census measurement (battr_logs 0.1.0, unchanged) plus the norm check and both replays,
    from {topic: {multi_id: {field: array}}} and the log's initial parameters."""
    import numpy as np
    import battr_logs as bl
    params = params or {}
    census = bl.measure_data(data, params)
    out = {"read": census.get("read"),
           "census": {k: census[k] for k in CENSUS_KEYS if k in census}}
    if not census.get("measurable"):
        out["replayed"] = False
        out["why_not"] = census.get("why_not") or census.get("read")
        return out
    n_cells = census.get("cells")
    b = data["battery_status"][census["battery_instance"]]
    if not n_cells or "voltage_v" not in b:
        out["replayed"] = False
        out["why_not"] = "no cell count" if not n_cells else "no voltage"
        return out
    t = np.asarray(b["timestamp"], dtype=np.int64)
    volt = np.asarray(b["voltage_v"], dtype=np.float64)
    cur = np.asarray(b["current_a"], dtype=np.float64)
    est = np.asarray(b[bl.FIELD], dtype=np.float64)
    cov = np.asarray(b[bl.COV], dtype=np.float64)
    conn = np.asarray(b["connected"]).astype(bool) if "connected" in b else np.ones(len(t), dtype=bool)
    cc = np.asarray(b["cell_count"]) if "cell_count" in b else None
    ld = (data.get("vehicle_land_detected") or {}).get(0)
    st = (data.get("vehicle_status") or {}).get(0)
    land = (ld["timestamp"], ld["landed"]) if ld and "landed" in ld else None
    status = (st["timestamp"], st["arming_state"]) if st and "arming_state" in st else None
    mask, _ = bl.airborne_mask(t, land, status)
    good = mask & np.isfinite(est) & np.isfinite(cur) & (cur >= 0)
    out["norm"] = norm_check(t, cov, volt, conn, cc, n_cells, mask)
    g = replay(t, volt, cur, conn, n_cells, gated=True)
    u = replay(t, volt, cur, conn, n_cells, gated=False, dtype=np.float64)
    gp = g["r_pub"][good]
    m = len(gp)
    zero = gp <= 0.0
    graw = g["r_raw"][good][m // 2:]
    graw = graw[np.isfinite(graw)]
    upd = g["updated"] & good
    out["replayed"] = True
    out["replay"] = {
        "collapsed": bool(np.all(zero[m // 2:])),
        "zero_share": round(float(np.mean(zero)), 4),
        "r_end_mohm": round(float(gp[-1]) * 1000.0, 4) if math.isfinite(gp[-1]) else None,
        "raw_median_second_half_mohm": round(float(np.median(graw)) * 1000.0, 4) if len(graw) else None,
        "updates_air": int(np.sum(upd)),
        "accepted_air": int(np.sum(g["accepted"] & good)),
        "accepted_total": int(np.sum(g["accepted"])),
        "norm_end": float(g["norm"][good][-1]),
        "norm_min": float(np.nanmin(g["norm"])),
    }
    raw = u["r_raw"][good][m // 2:]
    raw = raw[np.isfinite(raw)]
    med = float(np.median(raw)) if len(raw) else None
    out["plain"] = {"median_second_half_mohm": round(med * 1000.0, 4) if med is not None else None,
                    "positive": (med > 0.0) if med is not None else None,
                    "norm_min": float(np.nanmin(u["norm"]))}
    return out


def measure_file(path):
    from pyulog import ULog
    import battr_logs as bl
    u = ULog(path, list(bl.TOPICS), True)
    data = {}
    for d in u.data_list:
        data.setdefault(d.name, {})[d.multi_id] = d.data
    return measure_replay(data, dict(u.initial_parameters))


# ---------------------------------------------------------------- the flights to re-read

def rows_of(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def load_targets(named_paths):
    """Measurable flights of the earlier passes, collapsed first, then by pass and order; one
    entry per log id. named_paths: [(pass name, rows path)] in PASSES order."""
    rank = {name: k for k, name in enumerate(PASSES)}
    out, seen = [], set()
    for name, path in named_paths:
        for r in rows_of(path):
            m = r.get("measure") or {}
            if m.get("read") != "ok" or not m.get("measurable") or not r.get("log_id"):
                continue
            if r["log_id"] in seen:
                continue
            seen.add(r["log_id"])
            out.append({"pass": name, "pass_order": r.get("order"), "log_id": r["log_id"],
                        "vehicle": r.get("vehicle"), "sys_hw": r.get("sys_hw"),
                        "mav_type": r.get("mav_type"), "ver_sw_release": r.get("ver_sw_release"),
                        "row_collapsed": bool(m.get("collapsed")),
                        "row_step_r": (m.get("step") or {}).get("r"),
                        "row_r_end_mohm": m.get("r_end_mohm")})
    out.sort(key=lambda x: (not x["row_collapsed"], rank.get(x["pass"], 9), x["pass_order"] or 0))
    return out


def run(targets, fetch_fn, measure_fn, write, tmp_path, max_full=230, sleep=time.sleep,
        clock=time.time, budget_s=300 * 60, spacing=6.0):
    """Re-reads the targets in order until max_full logs were downloaded, the list or the budget
    ran out, Flight Review blocked a request, or five requests in a row failed."""
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
            rec["bytes"] = info.get("bytes")
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


# ---------------------------------------------------------------- grading

def step_r(m):
    r = ((m.get("census") or {}).get("step") or {}).get("r")
    return None if r is None else float(r)


def _share(k, n):
    return round(k / float(n), 4) if n else None


def grade(measures):
    """(predictions, report) from the measure dicts of replayed flights, with the plan's rules."""
    import battr_logs as bl
    ms = [m for m in measures if m.get("replayed")]
    norms = [m["norm"] for m in ms]
    c1k = sum(1 for x in norms if x["unexplained"] == 0)
    c2k = sum(1 for x in norms if x["min_norm"] is not None and x["min_norm"] >= FLOOR_TOL)
    alive = [m for m in ms if not m["census"].get("collapsed")]
    col = [m for m in ms if m["census"].get("collapsed")]
    flat = [m for m in col if step_r(m) is not None and step_r(m) > R_FLAT]
    steep = [m for m in col if step_r(m) is not None and step_r(m) <= R_FLAT]
    p1k = sum(1 for m in alive if not m["replay"]["collapsed"])
    p2k = sum(1 for m in flat if m["replay"]["collapsed"])
    p3k = sum(1 for m in steep if not m["replay"]["collapsed"])
    pairs = [(m["replay"]["r_end_mohm"], m["census"]["r_end_mohm"]) for m in alive
             if m["census"].get("step_valid") and (m["census"].get("r_end_mohm") or 0) > 0
             and m["replay"].get("r_end_mohm") is not None]
    rho = bl.spearman([a for a, _ in pairs], [b for _, b in pairs])
    p5k = sum(1 for m in steep if m["plain"].get("positive") is True)
    p = {
        "C1": (c1k / float(len(norms)) >= C1_SHARE) if len(norms) >= C_MIN else None,
        "C2": (c2k / float(len(norms)) >= C2_SHARE) if len(norms) >= C_MIN else None,
        "P1": (p1k / float(len(alive)) >= P1_SHARE) if len(alive) >= P1_MIN else None,
        "P2": (p2k / float(len(flat)) >= P2_SHARE) if len(flat) >= P2_MIN else None,
        "P3": (p3k / float(len(steep)) >= P3_SHARE) if len(steep) >= P3_MIN else None,
        "P4": (rho is not None and rho >= P4_RHO) if len(pairs) >= P4_MIN else None,
        "P5": (p5k / float(len(steep)) >= P5_SHARE) if len(steep) >= P5_MIN else None,
    }
    rep = {
        "replayed": len(ms), "collapsed": len(col), "alive": len(alive),
        "c1_no_unexplained_increase": c1k, "c1_share": _share(c1k, len(norms)),
        "c2_above_floor": c2k, "c2_share": _share(c2k, len(norms)),
        "p1_alive_not_collapsed_in_replay": p1k, "p1_share": _share(p1k, len(alive)),
        "flat_collapsed": len(flat), "p2_reproduced": p2k, "p2_share": _share(p2k, len(flat)),
        "steep_collapsed": len(steep), "p3_not_reproduced": p3k, "p3_share": _share(p3k, len(steep)),
        "p4_pairs": len(pairs), "p4_rho": round(rho, 4) if rho is not None else None,
        "p5_plain_positive": p5k, "p5_share": _share(p5k, len(steep)),
        "collapsed_without_r": len(col) - len(flat) - len(steep),
        "replay_collapsed_all": sum(1 for m in ms if m["replay"]["collapsed"]),
        "air_accept_share_quantiles": _quantiles([x["air_accept_share"] for x in norms]),
        "end_norm_over_floor_quantiles": _quantiles([x["end_norm"] / NORM_FLOOR for x in norms
                                                     if x["end_norm"] is not None]),
        "min_norm_quantiles": _quantiles([x["min_norm"] for x in norms]),
        "unexplained_increase_logs": [{"max_rel": x["max_unexplained_rel"], "n": x["unexplained"]}
                                      for x in norms if x["unexplained"]],
        "replay_accepted_air_share_quantiles": _quantiles(
            [m["replay"]["accepted_air"] / float(m["replay"]["updates_air"]) for m in ms
             if m["replay"]["updates_air"]]),
        "collapsed_rows": [{"step_r": step_r(m), "step_valid": m["census"].get("step_valid"),
                            "first_zero_s": m["census"].get("first_zero_s"),
                            "logged_r_end_mohm": m["census"].get("r_end_mohm"),
                            "replay_collapsed": m["replay"]["collapsed"],
                            "replay_r_end_mohm": m["replay"]["r_end_mohm"],
                            "replay_raw_median_mohm": m["replay"]["raw_median_second_half_mohm"],
                            "plain_median_mohm": m["plain"]["median_second_half_mohm"],
                            "air_accept_share": m["norm"]["air_accept_share"]} for m in col],
    }
    return p, rep


def _quantiles(xs):
    import statistics
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None

    def q(p):
        return round(xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))], 4)
    return {"n": len(xs), "p10": q(0.10), "p25": q(0.25), "median": round(statistics.median(xs), 4),
            "p75": q(0.75), "p90": q(0.90), "max": round(xs[-1], 4)}


def agreement(recs):
    """How often the recomputed census flag equals the earlier row's (same code, same log)."""
    same = diff = 0
    for r in recs:
        m = r.get("measure") or {}
        c = m.get("census") or {}
        if "collapsed" not in c:
            continue
        if bool(c["collapsed"]) == bool(r.get("row_collapsed")):
            same += 1
        else:
            diff += 1
    return {"same": same, "different": diff}


def main(argv=None):
    import urllib.request
    import battr_logs as bl
    import gnss_logs as gn
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--plan", default="PLAN-replay.md")
    ap.add_argument("--first-pass", default="first-pass-rows.jsonl")
    ap.add_argument("--onset-pass", default="onset-rows.jsonl")
    ap.add_argument("--steps-pass", default="steps-rows.jsonl")
    ap.add_argument("--budget-min", type=float, default=300)
    ap.add_argument("--max-full", type=int, default=230)
    ap.add_argument("--wait-min", type=float, default=60)
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)

    def get_plan():
        req = urllib.request.Request(PLAN_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    if not gn.wait_for_plan(args.plan, get_plan, args.wait_min * 60):
        print("PLAN-replay.md is not on main: no log read", flush=True)
        with open(os.path.join(args.out_dir, "replay-summary.json"), "w") as f:
            json.dump({"replay": __version__, "stop": "plan not on main; no log read"}, f, indent=1)
        return 3
    targets = load_targets([("first", args.first_pass), ("onset", args.onset_pass),
                            ("steps", args.steps_pass)])
    n_col = sum(1 for x in targets if x["row_collapsed"])
    assert len(targets) >= 200 and n_col >= 20, "earlier rows look wrong: %d flights, %d collapsed" % (
        len(targets), n_col)
    print(json.dumps({"targets": len(targets), "collapsed": n_col}), flush=True)
    started = time.time()
    recs = []
    tmp = os.path.join(tempfile.gettempdir(), "replay-log.ulg")
    with open(os.path.join(args.out_dir, "replay-rows.jsonl"), "w", encoding="utf-8") as out:
        def write(rec):
            recs.append(rec)
            out.write(json.dumps(rec, default=gn.plain) + "\n")
            out.flush()
            m = rec.get("measure") or {}
            rp = m.get("replay") or {}
            print("%d %s %s %s logged=%s replay=%s plain=%s" % (
                rec["order"], rec["pass"], rec["log_id"], rec["status"], rec["row_collapsed"],
                rp.get("collapsed"), (m.get("plain") or {}).get("median_second_half_mohm")), flush=True)
        res = run(targets, bl.fetch_one, measure_file, write, tmp, max_full=args.max_full,
                  budget_s=args.budget_min * 60, spacing=gn.SPACING_S)
    pred, rep = grade([r["measure"] for r in recs if r.get("measure")])
    summary = {"replay": __version__, "battr_logs": bl.__version__, "predictions": pred, "reported": rep,
               "census_agreement": agreement(recs),
               "status_counts": _counts(r.get("status") for r in recs),
               "why_not": _counts((r.get("measure") or {}).get("why_not") for r in recs
                                  if r.get("measure") and not r["measure"].get("replayed")),
               "targets": len(targets), "targets_collapsed": n_col, "pass": res,
               "wall_s": round(time.time() - started, 1)}
    text = json.dumps(summary, indent=1, default=gn.plain)
    with open(os.path.join(args.out_dir, "replay-summary.json"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text, flush=True)
    return 0


def _counts(xs):
    out = {}
    for x in xs:
        k = str(x)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


if __name__ == "__main__":
    sys.exit(main())

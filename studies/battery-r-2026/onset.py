"""onset.py: when does PX4's battery-resistance estimate collapse to 0: on the ground before
takeoff, or in flight? (px4-log-health study battery-r-2026, follow-up pre-registered in
studies/battery-r-2026/PLAN-onset.md)

It reuses the first census's code unchanged (battr_logs.py 0.1.0: the list, the streaming reader,
the pass and the measurement), skips every vehicle the first census requested, and adds, for each
collapsed log, where the collapse starts: the sample after the last positive estimate up to the
end of the flight, with its phase (airborne, armed on ground, disarmed), its current and its time
from takeoff. No position is read or written out.

Before any log is read, it checks that PLAN-onset.md on px4-log-health's main matches the local
copy, waiting up to --wait-min minutes for it.

    python onset.py --out-dir out [--plan PLAN-onset.md] [--first-pass first-pass-rows.jsonl]
        [--budget-min 300] [--max-heads 600] [--max-full 150] [--wait-min 60]

Needs battr_logs.py 0.1.0 and gnss_logs.py 0.1.0 next to it.
By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import time

import battr_logs as bl
import gnss_logs as gn

__version__ = "0.1.0"

PLAN_URL = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/PLAN-onset.md"
PHASES = {2: "airborne", 1: "armed on ground", 0: "disarmed", -1: "unknown"}
BEFORE_LOG = "before the log"

# the plan's thresholds
O1_SHARE = 0.05
O2_SHARE = 0.5
O3_MAX_A = 2.0
O4_RHO = 0.5
MIN_N = 20
O2_MIN = 5
O3_MIN = 3
O4_MIN = 15


def phases(t, land=None, status=None):
    """Phase code at each time in `t` (us): 2 airborne (vehicle_land_detected.landed == 0), else 1
    armed on ground or 0 disarmed (vehicle_status.arming_state, 2 = armed), else -1 unknown."""
    import numpy as np
    t = np.asarray(t, dtype=np.int64)
    out = np.full(len(t), -1, dtype=np.int64)
    if status is not None and len(status[0]):
        ts = np.asarray(status[0], dtype=np.int64)
        armed = np.asarray(status[1]).astype(np.int64) == 2
        idx = np.searchsorted(ts, t, side="right") - 1
        ok = idx >= 0
        out[ok] = np.where(armed[idx[ok]], 1, 0)
    if land is not None and len(land[0]):
        air, _ = bl.airborne_mask(t, land, None)
        out[air] = 2
    return out


def measure_data(data, params=None):
    """The first census's measurement of one log, plus the onset of a collapse."""
    import numpy as np
    m = bl.measure_data(data, params)
    if not m.get("measurable"):
        return m
    b = data["battery_status"][m["battery_instance"]]
    t = np.asarray(b["timestamp"], dtype=np.int64)
    est = np.asarray(b[bl.FIELD], dtype=np.float64)
    cur = np.asarray(b["current_a"], dtype=np.float64)
    ld = (data.get("vehicle_land_detected") or {}).get(0)
    st = (data.get("vehicle_status") or {}).get(0)
    land = (ld["timestamp"], ld["landed"]) if ld and "landed" in ld else None
    status = (st["timestamp"], st["arming_state"]) if st and "arming_state" in st else None
    ph = phases(t, land, status)
    m["log_start_phase"] = PHASES[int(ph[0])]
    if not m["collapsed"]:
        return m
    mask, _ = bl.airborne_mask(t, land, status)
    good = mask & np.isfinite(est) & np.isfinite(cur) & (cur >= 0)   # the samples bl measured
    idx = np.nonzero(good)[0]
    k_first, k_end = int(idx[0]), int(idx[-1])
    head = est[:k_end + 1]
    pos = np.nonzero(np.isfinite(head) & (head > 0.0))[0]
    if len(pos) == 0:
        m["onset_phase"] = BEFORE_LOG
        m["onset_current_a"] = None
        m["onset_s_from_takeoff"] = None
        return m
    k = int(pos[-1]) + 1
    m["onset_phase"] = PHASES[int(ph[k])]
    c = float(cur[k])
    m["onset_current_a"] = round(c, 3) if np.isfinite(c) and c >= 0 else None
    m["onset_s_from_takeoff"] = round(float(t[k] - t[k_first]) / 1e6, 1)
    return m


def measure_file(path):
    from pyulog import ULog
    u = ULog(path, list(bl.TOPICS), True)
    data = {}
    for d in u.data_list:
        data.setdefault(d.name, {})[d.multi_id] = d.data
    return measure_data(data, dict(u.initial_parameters))


def before_takeoff(x):
    return x.get("onset_phase") == BEFORE_LOG or (x.get("onset_s_from_takeoff") is not None
                                                   and x["onset_s_from_takeoff"] < 0)


def summarise(recs):
    """The plan's predictions and the reported figures, from every record written."""
    downloaded = [r for r in recs if r.get("measure")]
    read = [r for r in downloaded if r["measure"].get("read") == "ok"]
    meas = [r for r in read if r["measure"].get("measurable")]
    m = [r["measure"] for r in meas]
    col = [x for x in m if x["collapsed"]]
    before = [x for x in col if before_takeoff(x)]
    currents = [x["onset_current_a"] for x in col if x.get("onset_current_a") is not None]
    pairs = [(x["r_end_mohm"], x["step"]["mohm_per_cell"]) for x in m
             if x["r_end_mohm"] > 0 and x.get("step_valid")]
    rho = bl.spearman([a for a, _ in pairs], [b for _, b in pairs])
    p = {}
    p["O1"] = (len(col) / len(m) >= O1_SHARE) if len(m) >= MIN_N else None
    p["O2"] = (len(before) / len(col) >= O2_SHARE) if len(col) >= O2_MIN else None
    p["O3"] = (statistics.median(currents) < O3_MAX_A) if len(currents) >= O3_MIN else None
    p["O4"] = (rho is not None and rho >= O4_RHO) if len(pairs) >= O4_MIN else None
    rep = {
        "requests": len(recs),
        "status_counts": bl._counts(r.get("status", "") if not str(r.get("status", "")).startswith("error")
                                    else "error" for r in recs),
        "downloaded": len(downloaded), "read_ok": len(read), "measurable": len(m),
        "why_not": bl._counts(r["measure"].get("why_not") for r in read if not r["measure"].get("measurable")),
        "collapsed": len(col),
        "collapsed_share": round(len(col) / len(m), 4) if m else None,
        "before_takeoff": len(before),
        "before_takeoff_share": round(len(before) / len(col), 4) if col else None,
        "onset_phases": bl._counts(x.get("onset_phase") for x in col),
        "onset_currents_a": sorted(currents),
        "onset_current_median_a": round(statistics.median(currents), 3) if currents else None,
        "onset_s_from_takeoff": sorted(x["onset_s_from_takeoff"] for x in col
                                       if x.get("onset_s_from_takeoff") is not None),
        "log_start_phases": bl._counts(x.get("log_start_phase") for x in m),
        "o4_pairs": len(pairs),
        "spearman_r_end_vs_step": round(rho, 4) if rho is not None else None,
        "r_end_mohm_alive_quantiles": bl._quantiles([x["r_end_mohm"] for x in m if not x["collapsed"]]),
        "r_internal_counts": bl._counts(bl.r_internal(r) for r in meas),
        "collapsed_by_r_internal": bl._counts(bl.r_internal(r) for r in meas if r["measure"]["collapsed"]),
        "collapsed_rows": [{"order": r.get("order"), "sys_hw": r.get("sys_hw"), "mav_type": r.get("mav_type"),
                            "release": r.get("ver_sw_release"), "r_internal": bl.r_internal(r),
                            "onset_phase": r["measure"].get("onset_phase"),
                            "onset_current_a": r["measure"].get("onset_current_a"),
                            "onset_s_from_takeoff": r["measure"].get("onset_s_from_takeoff"),
                            "log_start_phase": r["measure"].get("log_start_phase"),
                            "first_zero_s": r["measure"].get("first_zero_s")}
                           for r in meas if r["measure"]["collapsed"]],
    }
    return {"onset": __version__, "battr_logs": bl.__version__, "predictions": p, "reported": rep}


def first_pass_vehicles(path):
    out = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.add(json.loads(line)["vehicle"])
    return out


def main(argv=None):
    import ijson
    import urllib.request
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--plan", default="PLAN-onset.md")
    ap.add_argument("--first-pass", default="first-pass-rows.jsonl")
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
        print("PLAN-onset.md is not on main: no log read", flush=True)
        with open(os.path.join(args.out_dir, "onset-summary.json"), "w") as f:
            json.dump({"onset": __version__, "stop": "plan not on main; no log read"}, f, indent=1)
        return 3
    skip = first_pass_vehicles(args.first_pass)
    assert len(skip) >= 200, "first-pass rows look wrong: %d vehicles" % len(skip)
    started = time.time()
    req = urllib.request.Request(gn.DBINFO, headers={"User-Agent": bl.USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as resp:
        cands, counts = bl.build(ijson.items(gn.open_stream(resp), "item"))
    counts["candidates_before_skip"] = len(cands)
    cands = [c for c in cands if c["vehicle"] not in skip]
    counts["first_pass_vehicles_skipped"] = len(skip)
    counts["candidates"] = len(cands)
    counts["candidate_vehicles"] = len({c["vehicle"] for c in cands})
    print(json.dumps(counts), flush=True)
    recs = []
    tmp = os.path.join(tempfile.gettempdir(), "onset-log.ulg")
    with open(os.path.join(args.out_dir, "onset-rows.jsonl"), "w", encoding="utf-8") as out:
        def write(rec):
            recs.append(rec)
            out.write(json.dumps(rec, default=gn.plain) + "\n")
            out.flush()
            m = rec.get("measure") or {}
            print("%d %s %s %s %s %s %s" % (
                rec["order"], rec["mav_type"], rec["log_id"], rec["status"], m.get("measurable"),
                m.get("collapsed"), m.get("onset_phase")), flush=True)
        res = bl.run(cands, bl.fetch_one, measure_file, write, tmp, max_heads=args.max_heads,
                     max_full=args.max_full, budget_s=args.budget_min * 60)
    summary = summarise(recs)
    summary.update({"list": counts, "pass": res, "wall_s": round(time.time() - started, 1)})
    text = json.dumps(summary, indent=1, default=gn.plain)
    with open(os.path.join(args.out_dir, "onset-summary.json"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

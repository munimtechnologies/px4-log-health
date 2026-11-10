"""steps.py: do collapses of PX4's battery-resistance estimate at takeoff come with logged voltage
that does not fall with logged current? (px4-log-health study battery-r-2026, third pass,
pre-registered in studies/battery-r-2026/PLAN-steps.md)

It grades the plan twice: on the first census's rows (held out: H1-H4) and on a new pass over
public Flight Review logs that skips every vehicle of the first census and of the onset pass
(S1-S4). The measurement is onset.py 0.1.0's (battr_logs.py 0.1.0 unchanged plus the onset). No
position is read or written out.

Before any log is read, it checks that PLAN-steps.md on px4-log-health's main matches the local
copy, waiting up to --wait-min minutes for it.

    python steps.py --out-dir out [--plan PLAN-steps.md] [--first-pass first-pass-rows.jsonl]
        [--onset-pass onset-rows.jsonl] [--budget-min 300] [--max-heads 600] [--max-full 150]
        [--wait-min 60]

Needs battr_logs.py 0.1.0, onset.py 0.1.0 and gnss_logs.py 0.1.0 next to it.
By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import argparse
import json
import os
import sys
import tempfile
import time

__version__ = "0.1.0"

PLAN_URL = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/PLAN-steps.md"

# the plan's thresholds
R_FLAT = -0.1
S1_SHARE, S1_MIN = 0.60, 5
S2_SHARE, S2_MIN = 0.30, 20
S3_SHARE, S3_MIN = 0.25, 10
S4_SHARE, S4_MIN = 0.50, 3


def step_r(m):
    r = (m.get("step") or {}).get("r")
    return None if r is None else float(r)


def is_flat(m):
    r = step_r(m)
    return r is not None and r > R_FLAT


def at_takeoff(m):
    return bool(m.get("collapsed")) and m.get("first_zero_s") is not None and float(m["first_zero_s"]) == 0.0


def later(m):
    return bool(m.get("collapsed")) and m.get("first_zero_s") is not None and float(m["first_zero_s"]) > 0.0


def measures(recs):
    """The measurement of every measurable flight among the records."""
    out = []
    for r in recs:
        m = r.get("measure") or {}
        if m.get("read") == "ok" and m.get("measurable"):
            out.append(m)
    return out


def _share(k, n):
    return round(k / n, 4) if n else None


def grade(ms, prefix="S"):
    """(predictions, report) for a list of measurements, with the plan's rules."""
    take = [m for m in ms if at_takeoff(m)]
    take_r = [m for m in take if step_r(m) is not None]
    alive_r = [m for m in ms if not m.get("collapsed") and step_r(m) is not None]
    with_r = [m for m in ms if step_r(m) is not None]
    flat = [m for m in with_r if is_flat(m)]
    steep = [m for m in with_r if not is_flat(m)]
    late = [m for m in ms if later(m)]
    n_take_flat = sum(1 for m in take_r if is_flat(m))
    n_alive_flat = sum(1 for m in alive_r if is_flat(m))
    n_flat_col = sum(1 for m in flat if m.get("collapsed"))
    n_late_valid = sum(1 for m in late if m.get("step_valid"))
    p = {
        prefix + "1": (n_take_flat / len(take_r) >= S1_SHARE) if len(take_r) >= S1_MIN else None,
        prefix + "2": (n_alive_flat / len(alive_r) <= S2_SHARE) if len(alive_r) >= S2_MIN else None,
        prefix + "3": (n_flat_col / len(flat) >= S3_SHARE) if len(flat) >= S3_MIN else None,
        prefix + "4": (n_late_valid / len(late) >= S4_SHARE) if len(late) >= S4_MIN else None,
    }
    rep = {
        "measurable": len(ms), "with_r": len(with_r),
        "collapsed": sum(1 for m in ms if m.get("collapsed")),
        "at_takeoff": len(take), "at_takeoff_with_r": len(take_r), "at_takeoff_flat": n_take_flat,
        "at_takeoff_flat_share": _share(n_take_flat, len(take_r)),
        "at_takeoff_r": sorted(step_r(m) for m in take_r),
        "at_takeoff_valid": sum(1 for m in take if m.get("step_valid")),
        "alive_with_r": len(alive_r), "alive_flat": n_alive_flat,
        "alive_flat_share": _share(n_alive_flat, len(alive_r)),
        "alive_valid": sum(1 for m in ms if not m.get("collapsed") and m.get("step_valid")),
        "flat": len(flat), "flat_collapsed": n_flat_col, "flat_collapsed_share": _share(n_flat_col, len(flat)),
        "steep": len(steep), "steep_collapsed": sum(1 for m in steep if m.get("collapsed")),
        "steep_collapsed_share": _share(sum(1 for m in steep if m.get("collapsed")), len(steep)),
        "later": len(late), "later_valid": n_late_valid, "later_valid_share": _share(n_late_valid, len(late)),
        "later_r": sorted(step_r(m) for m in late if step_r(m) is not None),
        "later_first_zero_s": sorted(float(m["first_zero_s"]) for m in late),
    }
    return p, rep


def rows_of(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def skip_vehicles(*paths):
    out = set()
    for p in paths:
        for r in rows_of(p):
            if r.get("vehicle"):
                out.add(r["vehicle"])
    return out


def collapsed_rows(recs):
    out = []
    for r in recs:
        m = r.get("measure") or {}
        if m.get("read") == "ok" and m.get("measurable") and m.get("collapsed"):
            out.append({"order": r.get("order"), "log_id": r.get("log_id"), "sys_hw": r.get("sys_hw"),
                        "mav_type": r.get("mav_type"), "release": r.get("ver_sw_release"),
                        "r_internal": (r.get("params") or {}).get("BAT1_R_INTERNAL"),
                        "first_zero_s": m.get("first_zero_s"), "onset_phase": m.get("onset_phase"),
                        "onset_current_a": m.get("onset_current_a"), "step": m.get("step"),
                        "step_valid": m.get("step_valid")})
    return out


def main(argv=None):
    import urllib.request
    import ijson
    import battr_logs as bl
    import gnss_logs as gn
    import onset as on
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--plan", default="PLAN-steps.md")
    ap.add_argument("--first-pass", default="first-pass-rows.jsonl")
    ap.add_argument("--onset-pass", default="onset-rows.jsonl")
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
        print("PLAN-steps.md is not on main: no log read", flush=True)
        with open(os.path.join(args.out_dir, "steps-summary.json"), "w") as f:
            json.dump({"steps": __version__, "stop": "plan not on main; no log read"}, f, indent=1)
        return 3

    # held-out rows: the first census
    first = rows_of(args.first_pass)
    h_pred, h_rep = grade(measures(first), "H")
    h_rep["collapsed_rows"] = collapsed_rows(first)
    print(json.dumps({"held_out": h_pred}), flush=True)

    skip = skip_vehicles(args.first_pass, args.onset_pass)
    assert len(skip) >= 400, "earlier rows look wrong: %d vehicles" % len(skip)
    started = time.time()
    req = urllib.request.Request(gn.DBINFO, headers={"User-Agent": bl.USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as resp:
        cands, counts = bl.build(ijson.items(gn.open_stream(resp), "item"))
    counts["candidates_before_skip"] = len(cands)
    cands = [c for c in cands if c["vehicle"] not in skip]
    counts["earlier_vehicles_skipped"] = len(skip)
    counts["candidates"] = len(cands)
    counts["candidate_vehicles"] = len({c["vehicle"] for c in cands})
    print(json.dumps(counts), flush=True)
    recs = []
    tmp = os.path.join(tempfile.gettempdir(), "steps-log.ulg")
    with open(os.path.join(args.out_dir, "steps-rows.jsonl"), "w", encoding="utf-8") as out:
        def write(rec):
            recs.append(rec)
            out.write(json.dumps(rec, default=gn.plain) + "\n")
            out.flush()
            m = rec.get("measure") or {}
            print("%d %s %s %s %s %s %s" % (
                rec["order"], rec["mav_type"], rec["log_id"], rec["status"], m.get("measurable"),
                m.get("collapsed"), step_r(m)), flush=True)
        res = bl.run(cands, bl.fetch_one, on.measure_file, write, tmp, max_heads=args.max_heads,
                     max_full=args.max_full, budget_s=args.budget_min * 60)
    s_pred, s_rep = grade(measures(recs), "S")
    s_rep["collapsed_rows"] = collapsed_rows(recs)
    summary = {"steps": __version__, "onset": on.__version__, "battr_logs": bl.__version__,
               "predictions": s_pred, "reported": s_rep,
               "held_out": {"predictions": h_pred, "reported": h_rep},
               "onset_style": on.summarise(recs),
               "list": counts, "pass": res, "wall_s": round(time.time() - started, 1)}
    text = json.dumps(summary, indent=1, default=gn.plain)
    with open(os.path.join(args.out_dir, "steps-summary.json"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

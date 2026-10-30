"""fleet_health.py: how healthy are public PX4 flights? Scores
studies/fleet-health-2026/PLAN.md.

Draws real (non-simulated) PX4 Flight Review logs of at least 4 minutes from
2025-10-01 on, in a new seeded order, one log per vehicle, leaving out every
log and every vehicle that px4-gps-lag's fleet-2026 run drew. Each log is
downloaded 10 s apart, measured with health.py and with px4-gps-lag's
gps_lag.py (an independent replication of fleet-2026's lag figures), and
deleted. No position is read. Vehicle ids are published only as a hash.

    python fleet_health.py --out-dir out --exclude v1-logs.jsonl --max-logs 300
    python fleet_health.py --out-dir out --rescore logs.jsonl

Uses fleet.py (px4-gps-lag 0.1.1) for the public log list and the download,
so the eligibility rules and the stop-on-block are the same as fleet-2026's.

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import time

import fleet
import gps_lag as gl
import health

__version__ = "0.1.0"

SEED = 20261031
DELAY_S = 10.0
LAG_KEYS = ("passed", "reason", "lag_ms", "se_ms", "param_gps_delay_ms", "lag_minus_param_ms",
            "gps_bus", "ver_sw_release", "gps_logged_dt_s", "pairs", "r2", "excitation_m_s2")
BASELINE_FIELDS = ("flight_s", "vib_median", "vib_p95", "clip_per_min", "jam_median", "jam_p95",
                   "sats_median", "eph_median", "sat_share")


def excluded(path):
    """Log ids and hashed vehicle ids an earlier run drew."""
    logs, vehicles = set(), set()
    if path:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    logs.add(str(r.get("log_id")))
                    if r.get("vehicle"):
                        vehicles.add(r["vehicle"])
    return logs, vehicles


def draw(candidates, skip_logs, skip_vehicles, seed=SEED):
    """One log per vehicle, vehicles in a seeded random order, nothing from
    skip_logs or skip_vehicles."""
    keep = [c for c in candidates
            if c["log_id"] not in skip_logs and (not c["vehicle"] or c["vehicle"] not in skip_vehicles)]
    order, by_vehicle = fleet.plan_order(keep, seed=seed)
    return [by_vehicle[key][0] for key in order]


def _share(rows, test):
    return round(sum(1 for r in rows if test(r)) / len(rows), 4) if rows else None


def _within(x, lo, hi):
    return None if x is None else lo <= x <= hi


def _quantiles(xs):
    xs = sorted(float(x) for x in xs if x is not None)
    if not xs:
        return None

    def q(p):
        k = (len(xs) - 1) * p
        lo = int(k)
        hi = min(lo + 1, len(xs) - 1)
        return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 4)
    return {"n": len(xs), "p10": q(0.10), "p25": q(0.25), "p50": q(0.50), "p75": q(0.75),
            "p90": q(0.90), "p99": q(0.99)}


def summarise(rows):
    """The PLAN's checks, from the per-log rows."""
    ok = [r for r in rows if r.get("health_ok")]
    vehicles = {r.get("vehicle") or "log:" + r["log_id"] for r in ok}
    vib = [r for r in ok if r.get("vib_median") is not None]
    clip = [r for r in ok if r.get("clip_count") is not None]
    jam = [r for r in ok if r.get("jam_median") is not None]
    jstate = [r for r in ok if r.get("jam_state_reported") and r.get("jam_state_max") is not None]
    spoof = [r for r in ok if r.get("spoof_state_reported") and r.get("spoof_state_max") is not None]
    batt = [r for r in ok if r.get("battery_warning_max") is not None]
    fs = [r for r in ok if r.get("failsafe_in_flight") is not None]
    sat = [r for r in ok if r.get("sat_share") is not None]
    vib_clip = [r for r in vib if r.get("clip_count") is not None]
    loud = [r for r in vib_clip if r["vib_band"] in ("orange", "red")]
    quiet = [r for r in vib_clip if r["vib_band"] == "green"]

    s = {
        "vib_orange_or_red": _share(vib, lambda r: r["vib_band"] in ("orange", "red")),
        "vib_red": _share(vib, lambda r: r["vib_band"] == "red"),
        "clip_any": _share(clip, lambda r: r["clip_count"] > 0),
        "clip_any_loud": _share(loud, lambda r: r["clip_count"] > 0),
        "clip_any_quiet": _share(quiet, lambda r: r["clip_count"] > 0),
        "jam_median_over_40": _share(jam, lambda r: r["jam_median"] > health.JAM_OK),
        "jam_p95_80_or_more": _share(jam, lambda r: r["jam_p95"] >= health.JAM_HIGH),
        "jam_state_warning_or_worse": _share(jstate, lambda r: r["jam_state_max"] >= 2),
        "spoof_indicated_or_worse": _share(spoof, lambda r: r["spoof_state_max"] >= 2),
        "battery_low_or_worse": _share(batt, lambda r: r["battery_warning_max"] >= 1),
        "battery_critical_or_worse": _share(batt, lambda r: r["battery_warning_max"] >= 2),
        "failsafe": _share(fs, lambda r: r["failsafe_in_flight"]),
        "saturated": _share(sat, lambda r: r["sat_share"] >= health.SAT_SHARE),
        "any_flag": _share(ok, lambda r: bool(r.get("flags"))),
    }
    n = {"vib": len(vib), "clip": len(clip), "loud": len(loud), "quiet": len(quiet), "jam": len(jam),
         "jam_state": len(jstate), "spoof": len(spoof), "battery": len(batt), "failsafe": len(fs),
         "saturation": len(sat)}

    def enough(key, k=30):
        return n[key] >= k

    lag_rows = []
    for r in rows:
        if r.get("lag"):
            lag_rows.append(dict(r["lag"], log_id=r["log_id"], vehicle=r.get("vehicle")))
    lag = fleet.summarise(lag_rows) if lag_rows else None
    lag_median = lag["lag_ms"]["median"] if lag else None
    lag_far = lag["lag_minus_param_ms"]["share_abs_over_50"] if lag else None

    checks = {
        "E1": len(ok) >= 150 and len(vehicles) >= 100,
        "P1": _within(s["vib_orange_or_red"], 0.03, 0.30) if enough("vib") else None,
        "P2": (s["vib_red"] <= 0.08) if enough("vib") else None,
        "P3": _within(s["clip_any"], 0.05, 0.50) if enough("clip") else None,
        "P4": (s["clip_any_loud"] > s["clip_any_quiet"]) if n["loud"] >= 10 and n["quiet"] >= 10 else None,
        "P5": (s["jam_median_over_40"] <= 0.20) if enough("jam") else None,
        "P6": (s["jam_state_warning_or_worse"] <= 0.15) if enough("jam_state") else None,
        "P7": (s["spoof_indicated_or_worse"] <= 0.10) if enough("spoof") else None,
        "P8": _within(s["battery_low_or_worse"], 0.03, 0.30) if enough("battery") else None,
        "P9": _within(s["failsafe"], 0.03, 0.35) if enough("failsafe") else None,
        "P10": (s["saturated"] <= 0.20) if enough("saturation") else None,
        "R1": (_within(lag_median, 80, 120) if lag and lag["passed"] >= 60 else None),
        "R2": (_within(lag_far, 0.18, 0.42) if lag and lag["passed"] >= 60 else None),
    }
    checks = {k: (None if v is None else bool(v)) for k, v in checks.items()}
    reasons = {}
    for r in rows:
        if not r.get("health_ok"):
            reasons[r.get("health_reason") or "?"] = reasons.get(r.get("health_reason") or "?", 0) + 1
    if lag:
        lag = {k: lag[k] for k in ("passed", "vehicles_passed", "lag_ms", "median_se_ms", "param",
                                   "lag_minus_param_ms", "by_bus", "not_passed_reasons")}
    return {
        "attempted": len(rows),
        "downloaded": sum(r.get("download") == "ok" for r in rows),
        "health_ok": len(ok),
        "vehicles_ok": len(vehicles),
        "shares": s,
        "n": n,
        "flags_per_log": {str(k): sum(1 for r in ok if len(r.get("flags") or []) == k) for k in range(6)},
        "replication": lag,
        "not_ok_reasons": reasons,
        "checks": checks,
        "decision": {"B1_publish_baseline": checks["E1"],
                     "R_replicated": bool(checks["R1"] and checks["R2"]),
                     "R_caution": checks["R1"] is False},
    }


def baseline(rows):
    """Fleet quantiles of each figure, for health.py to place a log against."""
    ok = [r for r in rows if r.get("health_ok")]
    return {
        "note": ("Quantiles over the public PX4 Flight Review logs of studies/fleet-health-2026 "
                 "(one log per vehicle, real flights of at least 4 minutes from 2025-10-01 on). "
                 "Data derived from Flight Review logs, CC BY 4.0."),
        "fields": {k: _quantiles([r.get(k) for r in ok]) for k in BASELINE_FIELDS},
        "vib_band": {b: sum(1 for r in ok if r.get("vib_band") == b) for b in ("green", "orange", "red")},
    }


def explore(rows):
    """Breakdowns the PLAN did not predict, labelled exploratory."""
    ok = [r for r in rows if r.get("health_ok")]

    def by(key):
        out = {}
        for r in ok:
            k = key(r)
            out.setdefault(k, []).append(r)
        return {k: {"n": len(v), "any_flag": _share(v, lambda r: bool(r.get("flags"))),
                    "vib_orange_or_red": _share([r for r in v if r.get("vib_band")],
                                                lambda r: r["vib_band"] in ("orange", "red"))}
                for k, v in sorted(out.items(), key=lambda kv: str(kv[0]))}
    return {
        "note": "not pre-registered; descriptive only",
        "by_mav_type": by(lambda r: r.get("mav_type") or "unknown"),
        "by_multicopter": by(lambda r: str(r.get("multicopter"))),
        "vib_worst_imu_orange_or_red": _share([r for r in ok if r.get("vib_median_worst_imu") is not None],
                                              lambda r: r["vib_median_worst_imu"] >= health.VIB_ORANGE),
        "jam_reported": _share([r for r in ok if "jam_reported" in r], lambda r: r["jam_reported"]),
        "spoof_state_reported": _share([r for r in ok if "spoof_state_reported" in r],
                                       lambda r: r["spoof_state_reported"]),
        "gnss_topic": {t: sum(1 for r in ok if r.get("gnss_topic") == t)
                       for t in ("vehicle_gps_position", "sensor_gps", None)},
    }


def _write(out_dir, rows, extra):
    summary = summarise(rows)
    summary["exploratory"] = explore(rows)
    summary.update({"fleet_health": __version__, "health": health.__version__, "gps_lag": gl.__version__,
                    "fleet": fleet.__version__, "seed": SEED})
    summary.update(extra)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    with open(os.path.join(out_dir, "baseline.json"), "w", encoding="utf-8") as f:
        json.dump(baseline(rows), f, indent=1)
    print(json.dumps(summary, indent=1), flush=True)


def measure_one(path):
    """Health and GPS lag of one downloaded log."""
    row = {}
    try:
        row.update(health.measure_health(health.read_health(path)))
    except Exception as exc:  # a log pyulog or health.py cannot read
        row.update({"health_ok": False, "health_reason": "error: %s" % str(exc)[:200]})
    try:
        m = gl.measure(gl.read_log(path))
        if m.get("gps_bus") == "simulation":
            m.update({"passed": False, "reason": "simulated GPS"})
    except Exception as exc:
        m = {"passed": False, "reason": "error: %s" % str(exc)[:200]}
    row["lag"] = {k: (float(m[k]) if isinstance(m.get(k), float) else m.get(k)) for k in LAG_KEYS if k in m}
    return row


def main(argv=None):
    import requests
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out-dir", required=True)
    p.add_argument("--exclude", help="an earlier run's logs.jsonl: its logs and vehicles are left out")
    p.add_argument("--budget-min", type=float, default=150)
    p.add_argument("--max-logs", type=int, default=300)
    p.add_argument("--tmp", default="/tmp/px4-log-health")
    p.add_argument("--rescore", help="score an existing logs.jsonl instead of downloading")
    args = p.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)
    if args.rescore:
        with open(args.rescore, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        _write(args.out_dir, rows, {"rescored_from": os.path.basename(args.rescore)})
        return 0
    os.makedirs(args.tmp, exist_ok=True)
    started = time.time()
    session = requests.Session()
    session.headers["User-Agent"] = fleet.USER_AGENT.replace("px4-gps-lag/%s" % fleet.__version__,
                                                             "px4-log-health/%s" % __version__)
    candidates, counts = fleet.load_candidates(session=session)
    skip_logs, skip_vehicles = excluded(args.exclude)
    todo = draw(candidates, skip_logs, skip_vehicles)
    print("listed %d, eligible %d, after exclusions %d vehicles (%.0f s)"
          % (counts["listed"], counts["eligible"], len(todo), time.time() - started), flush=True)

    rows, blocked = [], None
    path = os.path.join(args.tmp, "log.ulg")
    with open(os.path.join(args.out_dir, "logs.jsonl"), "w", encoding="utf-8") as out:
        for c in todo:
            if len(rows) >= args.max_logs or time.time() - started > args.budget_min * 60:
                break
            if rows:
                time.sleep(DELAY_S)
            row = dict(c, attempt=len(rows) + 1)
            t0 = time.time()
            try:
                row["download"] = fleet.download(session, c["log_id"], path)
            except fleet.Blocked as exc:
                blocked = str(exc)
                break
            row["download_s"] = round(time.time() - t0, 1)
            if row["download"] == "ok":
                row["size_mb"] = round(os.path.getsize(path) / 1e6, 1)
                row.update(measure_one(path))
                os.remove(path)
            else:
                row.update({"health_ok": False, "health_reason": "download " + row["download"],
                            "lag": {"passed": False, "reason": "download " + row["download"]}})
            rows.append(row)
            out.write(json.dumps(row) + "\n")
            out.flush()
            print("%d %s health %s flags %s lag %s" % (len(rows), c["log_id"], row.get("health_reason"),
                                                       len(row.get("flags") or []), row["lag"].get("lag_ms")),
                  flush=True)
    _write(args.out_dir, rows, {"list": counts, "excluded": {"logs": len(skip_logs), "vehicles": len(skip_vehicles)},
                                "blocked": blocked, "wall_s": round(time.time() - started, 1)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

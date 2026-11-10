"""innov.py: does EKF2's own GPS velocity innovation agree with gps_lag.py?

EKF2 fuses a GPS velocity measured L seconds before its timestamp as if it
were measured EKF2_GPS_DELAY (P) seconds before. PX4's innovation is the
prediction minus the measurement (estimator_innovations.gps_hvel; in the EKF2
source, _state.vel - velocity), so it is about v(t - P) - v(t - L)
= +a * (L - P) while the vehicle accelerates at a. The slope k of the
innovation against acceleration therefore estimates L - P, shrunk by however
much the filter's state has already followed the late GPS. This reads the
innovation and the IMU's north-east acceleration (from sensor_combined and
vehicle_attitude, as gps_lag.py does, averaged over +-0.1 s around the
innovation's fusion time) from a log and fits k with one constant per axis.
The implied excess lag is +k. It is an independent check of the direction of
gps_lag.py's lag - P, not a second measurement of the lag.

0.1.0 took the innovation to be measurement minus prediction and reported -k;
see studies/fleet-2026/GRADE-innov.md.

    python innov.py --rows logs.jsonl --out-dir out --budget-min 100

re-downloads the logs that passed gps_lag.py in a fleet run (fleet.py's
logs.jsonl, or px4-log-health's fleet_health.py logs.jsonl, whose lag figures
sit under "lag"), one at a time, 6 s apart, and scores the plan's checks.
Each log is deleted once measured; no position is read.

    python innov.py --rescore-v010 innov.jsonl --out-dir out

scores rows written by 0.1.0 again with PX4's sign, with no download.

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time

import numpy as np

import gps_lag as gl

__version__ = "0.2.0"

SMOOTH_S = 0.1          # acceleration is averaged over +-0.1 s around the fusion time
MIN_SAMPLES = 100
MIN_EXCITATION = 0.3    # m/s^2: RMS of the averaged acceleration about its mean
DELAY_S = 6.0
INNOV_TOPICS = ("estimator_innovations", "ekf2_innovations")


def read_innov(path):
    """GPS horizontal velocity innovations from a ULog file: {'t_log', 't_sample'
    (None when the log has no sample time), 'inn' (n, 2), 'topic'}."""
    from pyulog import ULog
    ulog = ULog(path, list(INNOV_TOPICS), True)
    data = {}
    for d in ulog.data_list:
        if d.name in INNOV_TOPICS and d.multi_id == 0:
            data[d.name] = d.data
    if "gps_hvel[0]" in data.get("estimator_innovations", {}):
        d, fields, topic = data["estimator_innovations"], ("gps_hvel[0]", "gps_hvel[1]"), "estimator_innovations"
    elif "vel_pos_innov[0]" in data.get("ekf2_innovations", {}):
        d, fields, topic = data["ekf2_innovations"], ("vel_pos_innov[0]", "vel_pos_innov[1]"), "ekf2_innovations"
    else:
        raise ValueError("no GPS velocity innovation in the log")
    t_log = d["timestamp"] / 1e6
    t_sample = None
    if "timestamp_sample" in d and np.all(d["timestamp_sample"] > 0):
        t_sample = d["timestamp_sample"] / 1e6
    inn = np.column_stack([d[fields[0]], d[fields[1]]]).astype(float)
    return {"t_log": t_log, "t_sample": t_sample, "inn": inn, "topic": topic}


def fusion_times(innov, param_ms):
    """When each innovation was formed: the logged sample time (EKF2's delayed
    horizon) if there is one, else the log time minus EKF2_GPS_DELAY."""
    if innov.get("t_sample") is not None:
        return np.asarray(innov["t_sample"], dtype=float), "timestamp_sample"
    return np.asarray(innov["t_log"], dtype=float) - (param_ms or 110.0) / 1e3, "timestamp - param"


def window_clear(t, imu_t, half=SMOOTH_S):
    """True where [t - half, t + half] is inside the IMU record with no gap
    over gps_lag.MAX_IMU_GAP_S."""
    t = np.asarray(t, dtype=float)
    imu_t = np.asarray(imu_t, dtype=float)
    ws, we = t - half, t + half
    keep = (ws >= imu_t[0]) & (we <= imu_t[-1])
    gaps = np.nonzero(np.diff(imu_t) > gl.MAX_IMU_GAP_S)[0]
    if len(gaps):
        overlapping = (np.searchsorted(imu_t[gaps], we, side="left")
                       - np.searchsorted(imu_t[gaps + 1], ws, side="right"))
        keep &= overlapping <= 0
    return keep


def fit_slope(a, y):
    """Slope k of y (n, 2) against a (n, 2), one constant per axis, both axes
    pooled; with its nominal standard error and the correlation."""
    a = np.asarray(a, dtype=float)
    y = np.asarray(y, dtype=float)
    ac = a - a.mean(axis=0)
    yc = y - y.mean(axis=0)
    saa = float(np.sum(ac * ac))
    syy = float(np.sum(yc * yc))
    if saa <= 0:
        return None
    k = float(np.sum(ac * yc)) / saa
    e = yc - k * ac
    n = len(a)
    s2 = float(np.sum(e * e)) / max(2 * n - 3, 1)
    return {"k_s": k, "se_s": float(np.sqrt(s2 / saa)),
            "r": float(np.sum(ac * yc)) / float(np.sqrt(saa * syy)) if syy > 0 else None,
            "excitation_m_s2": float(np.sqrt(saa / max(2 * n, 1)))}


def measure_innov(log, innov):
    """Implied excess lag (ms) of one log from its innovations. log is a dict
    as gps_lag.read_log returns; innov as read_innov returns."""
    param = log.get("facts", {}).get("param_gps_delay_ms")
    t_f, source = fusion_times(innov, param)
    inn = np.asarray(innov["inn"], dtype=float)
    imu_t = np.asarray(log["imu_t"], dtype=float)
    keep = np.all(np.isfinite(inn), axis=1) & ~np.all(inn == 0, axis=1)
    fresh = np.concatenate([[True], np.any(inn[1:] != inn[:-1], axis=1)])
    keep &= fresh
    keep &= gl.flying_at(t_f, log["land_t"], log["landed"])
    keep &= window_clear(t_f, imu_t)
    out = {"innov_topic": innov.get("topic"), "time_source": source,
           "innov_samples": int(np.sum(keep)), "param_gps_delay_ms": param}
    if out["innov_samples"] < MIN_SAMPLES:
        out.update({"passed": False, "reason": "too few in-flight innovations"})
        return out
    a_ne = gl.ne_accel(imu_t, log["f_body"], log["att_t"], log["q"])
    V = gl.cumulative(imu_t, a_ne)
    t = t_f[keep]
    a = np.column_stack([(np.interp(t + SMOOTH_S, imu_t, V[:, k]) - np.interp(t - SMOOTH_S, imu_t, V[:, k]))
                         / (2 * SMOOTH_S) for k in (0, 1)])
    fit = fit_slope(a, inn[keep])
    if fit is None or fit["excitation_m_s2"] < MIN_EXCITATION:
        out.update({"passed": False, "reason": "too little manoeuvring",
                    "excitation_m_s2": fit and fit["excitation_m_s2"]})
        return out
    out.update(fit)
    out["implied_excess_ms"] = round(fit["k_s"] * 1e3, 1)
    out["implied_se_ms"] = round(fit["se_s"] * 1e3, 1)
    out["passed"] = True
    out["reason"] = "ok"
    return out


def ranks(xs):
    """Ranks 1..n, ties sharing their mean rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for m in range(i, j + 1):
            r[order[m]] = (i + j) / 2.0 + 1
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 3:
        return None
    rx, ry = ranks(x), ranks(y)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    return sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else None


def summarise(rows):
    """The checks of studies/fleet-2026/PLAN-innov.md (and PLAN-innov-confirm.md)."""
    ok = [r for r in rows if r.get("passed") and r.get("lag_minus_param_ms") is not None]
    d = [float(r["lag_minus_param_ms"]) for r in ok]
    x = [float(r["implied_excess_ms"]) for r in ok]
    rho = spearman(d, x)
    far = [(a, b) for a, b in zip(d, x) if abs(a) > 50]
    near = [b for a, b in zip(d, x) if abs(a) <= 20]
    same_sign = (sum((a > 0) == (b > 0) for a, b in far) / len(far)) if far else None
    ratios = [b / a for a, b in far]
    ratio = statistics.median(ratios) if ratios else None
    far_abs = statistics.median([abs(b) for a, b in far]) if far else None
    near_abs = statistics.median([abs(b) for b in near]) if near else None
    checks = {
        "VE1": len(ok) >= 60,
        "V1": rho is not None and rho >= 0.3,
        "V2": (same_sign >= 0.70) if len(far) >= 10 else None,
        "V3": (0.2 <= ratio <= 1.2) if len(far) >= 10 else None,
        "V4": (near_abs < far_abs) if len(far) >= 10 and len(near) >= 10 else None,
    }
    checks = {k: (None if v is None else bool(v)) for k, v in checks.items()}
    if not checks["VE1"]:
        rule = "pilot"
    elif checks["V1"] and checks["V2"]:
        rule = "C1"
    else:
        rule = "C2"
    reasons = {}
    for r in rows:
        if not r.get("passed"):
            reasons[r.get("reason") or "?"] = reasons.get(r.get("reason") or "?", 0) + 1
    rnd = lambda v, n=3: None if v is None else round(v, n)
    return {
        "attempted": len(rows), "validated": len(ok),
        "spearman_rho": rnd(rho),
        "far": {"n": len(far), "same_sign_share": rnd(same_sign),
                "median_ratio_implied_to_measured": rnd(ratio),
                "median_abs_implied_ms": rnd(far_abs, 1)},
        "near": {"n": len(near), "median_abs_implied_ms": rnd(near_abs, 1)},
        "median_implied_excess_ms": rnd(statistics.median(x), 1) if x else None,
        "median_implied_se_ms": rnd(statistics.median([r["implied_se_ms"] for r in ok]), 1) if ok else None,
        "time_source": {s: sum(r.get("time_source") == s for r in ok)
                        for s in ("timestamp_sample", "timestamp - param")},
        "not_passed_reasons": reasons,
        "checks": checks,
        "decision": rule,
    }


def source_row(row):
    """The lag figures of one fleet row: fleet.py rows carry them at the top
    level, fleet_health.py rows under 'lag'."""
    lag = row.get("lag")
    if isinstance(lag, dict):
        out = dict(lag)
        out["log_id"] = row.get("log_id")
        out["vehicle"] = row.get("vehicle")
        return out
    return row


def flip_v010(row):
    """A row written by 0.1.0, in PX4's sign."""
    out = dict(row)
    if out.get("implied_excess_ms") is not None:
        out["implied_excess_ms"] = -out["implied_excess_ms"]
    out["sign"] = "flipped from 0.1.0"
    return out


def rescore_v010(path, out_dir):
    """Score 0.1.0's innov.jsonl again with PX4's sign, with no download."""
    with open(path, encoding="utf-8") as f:
        rows = [flip_v010(json.loads(line)) for line in f if line.strip()]
    summary = summarise(rows)
    summary.update({"innov": __version__, "rescored_from": os.path.basename(path),
                    "sign": "PX4 (prediction minus measurement); 0.1.0 rows negated"})
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1), flush=True)
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--rows", help="a fleet run's logs.jsonl")
    p.add_argument("--rescore-v010", help="innov.jsonl written by innov.py 0.1.0")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--budget-min", type=float, default=100)
    p.add_argument("--tmp", default="/tmp/px4-gps-lag-innov")
    args = p.parse_args(argv)
    if args.rescore_v010:
        rescore_v010(args.rescore_v010, args.out_dir)
        return 0
    if not args.rows:
        p.error("--rows or --rescore-v010 is needed")
    import requests
    import fleet
    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(args.tmp, exist_ok=True)
    with open(args.rows, encoding="utf-8") as f:
        source = [source_row(json.loads(line)) for line in f if line.strip()]
    todo = [r for r in source if r.get("passed")]
    print("%d logs passed gps_lag.py in the fleet run" % len(todo), flush=True)
    started = time.time()
    session = requests.Session()
    session.headers["User-Agent"] = fleet.USER_AGENT.replace("px4-gps-lag/", "px4-gps-lag-innov/")
    path = os.path.join(args.tmp, "log.ulg")
    rows, blocked = [], None
    with open(os.path.join(args.out_dir, "innov.jsonl"), "w", encoding="utf-8") as out:
        for i, src in enumerate(todo):
            if time.time() - started > args.budget_min * 60:
                break
            if i:
                time.sleep(DELAY_S)
            row = {"log_id": src["log_id"], "vehicle": src.get("vehicle"), "lag_ms": src.get("lag_ms"),
                   "lag_minus_param_ms": src.get("lag_minus_param_ms"), "gps_bus": src.get("gps_bus"),
                   "ver_sw_release": src.get("ver_sw_release")}
            try:
                row["download"] = fleet.download(session, src["log_id"], path)
            except fleet.Blocked as exc:
                blocked = str(exc)
                break
            if row["download"] == "ok":
                try:
                    row.update(measure_innov(gl.read_log(path), read_innov(path)))
                except Exception as exc:  # a log pyulog or this code cannot read
                    row.update({"passed": False, "reason": "error: %s" % str(exc)[:200]})
                os.remove(path)
            else:
                row.update({"passed": False, "reason": "download " + row["download"]})
            rows.append(row)
            out.write(json.dumps(row) + "\n")
            out.flush()
            print("%d %s %s implied %s ms vs measured %s ms (%s)"
                  % (len(rows), src["log_id"], row.get("passed"), row.get("implied_excess_ms"),
                     row.get("lag_minus_param_ms"), row.get("reason")), flush=True)
    summary = summarise(rows)
    summary.update({"innov": __version__, "gps_lag": gl.__version__, "blocked": blocked,
                    "rows_from": os.path.basename(args.rows), "wall_s": round(time.time() - started, 1)})
    with open(os.path.join(args.out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

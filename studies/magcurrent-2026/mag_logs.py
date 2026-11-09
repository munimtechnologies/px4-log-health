"""mag_logs.py: how much does motor current bend the compass? A census of public PX4 flight logs
(px4-log-health study magcurrent-2026, pre-registered in studies/magcurrent-2026/PLAN.md).

PX4's docs and Flight Review's 'Thrust and Magnetic Field' plot say the norm of the magnetic field
should stay constant in flight; if it tracks thrust or battery current, the motor current is
bending the compass and the yaw estimate suffers (fix: move the compass, or use PX4's compass
power compensation, CAL_MAG_COMP_TYP). Both are read one log at a time. This pass measures it
across public logs.

It reads Flight Review's public list once and keeps real logs (no simulation, no CI) of at least
120 s, dated 2025-01-01 or later, from v1.13 or newer or with no readable release, newest first,
one log per vehicle. For each it streams the log and reads only its definitions section. If the
formats have vehicle_magnetometer and battery_status, it downloads the rest (at most 100 MB) to a
temporary file and fits, over the airborne part of the flight, the norm of vehicle_magnetometer
instance 0 (calibrated and, if enabled, power-compensated by PX4) against the battery current:
norm = a + b * current. The coupling is |b| times the flight's current range (5th to 95th
percentile) as a percentage of the median norm. No position is written out, and the file is
deleted after it is read. Requests are 6 s apart, and the pass stops at the first block.

Before any log is read, it checks that studies/magcurrent-2026/PLAN.md on px4-log-health's main
matches the local copy (line endings normalised), waiting up to --wait-min minutes for it.

    python mag_logs.py --out-dir out [--plan PLAN.md] [--budget-min 300] [--max-heads 500]
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

PLAN_URL = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/magcurrent-2026/PLAN.md"
USER_AGENT = ("px4-log-health-magcurrent/%s (MV2 of Munim, Inc.; mv2@munimtech.com; "
              "research on public logs)" % __version__)
SINCE = "2025-01-01"
MIN_SERIES = (1, 13)
MIN_DURATION_S = 120
MAX_LOG_BYTES = 100 << 20
KEEP_PARAMS = ("CAL_MAG_COMP_TYP", "SENS_MAG_MODE", "EKF2_MAG_TYPE", "EKF2_MAG_CHECK", "SYS_HAS_MAG")
TOPICS = ("vehicle_magnetometer", "battery_status", "vehicle_land_detected", "vehicle_status",
          "sensor_mag", "estimator_status", "vehicle_thrust_setpoint")

# one log's measurement
MIN_PAIRS = 150             # 30 s of airborne pairs at the default 5 Hz
MIN_RANGE_A = 5.0
MIN_CURRENT_A = 0.5
MAX_GAP_S = 0.5
STRONG_PCT, STRONG_R = 10.0, 0.5
MODERATE_PCT, MODERATE_R = 5.0, 0.3

# the plan's thresholds
K1_SHARE = 0.5
K2_PCT = 5.0
K3_SHARE = 0.10
K4_SHARE = 0.05
K5_MIN, K5_RHO = 30, 0.1
K6_MIN = 5


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
        return None, "older than v1.13"
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
    return "vehicle_magnetometer" in formats and "battery_status" in formats


def fetch_one(log_id, path, opener=None, max_bytes=MAX_LOG_BYTES):
    """Streams one public log. Returns (info, path or None): the whole log is written to `path`
    only when its formats have vehicle_magnetometer and battery_status and it is at most max_bytes."""
    import urllib.request
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(gn.URL % log_id, headers={"User-Agent": USER_AGENT})
    with opener(req, timeout=300) as resp:
        chunks = gn.chunks_of(gn.open_stream(resp))
        formats, params, raw, complete = gn.read_definitions(chunks)
        info = {"head_complete": complete, "has_mag": "vehicle_magnetometer" in formats,
                "has_battery": "battery_status" in formats,
                "has_thrust_sp": "vehicle_thrust_setpoint" in formats, "params": pick(params)}
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


def bus_name(dev):
    return {0: "unknown", 1: "i2c", 2: "spi", 3: "uavcan", 4: "simulation", 5: "serial",
            6: "mavlink"}.get(int(dev) & 0x7, "unknown")


def most_common_id(ids):
    if ids is None:
        return None
    ids = [int(x) & 0xFFFFFFFF for x in ids if int(x)]
    if not ids:
        return None
    return max(sorted(set(ids)), key=ids.count)


def external_of(sensor_mag, dev):
    """is_external of the sensor_mag instance whose device id is `dev`, or None."""
    if not dev:
        return None
    for mid in sorted(sensor_mag):
        d = sensor_mag[mid]
        if "is_external" not in d or "device_id" not in d:
            continue
        if most_common_id(d["device_id"]) == dev and len(d["is_external"]):
            return bool(int(d["is_external"][0]))
    return None


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


def paired(tm, tb, values, max_gap_s=MAX_GAP_S):
    """(keep, v): `values` (sampled at tb, us) linearly interpolated at the times tm; keep is False
    where tm is outside tb's span or the nearest sample of tb is more than max_gap_s away."""
    import numpy as np
    tm = np.asarray(tm, dtype=np.float64)
    tb = np.asarray(tb, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    i = np.searchsorted(tb, tm)
    lo = np.clip(i - 1, 0, len(tb) - 1)
    hi = np.clip(i, 0, len(tb) - 1)
    gap = np.minimum(np.abs(tm - tb[lo]), np.abs(tb[hi] - tm))
    keep = (gap <= max_gap_s * 1e6) & (tm >= tb[0]) & (tm <= tb[-1])
    return keep, np.interp(tm, tb, values)


def coupling(x, norm):
    """Least-squares norm = a + b * x. pct = |b| * (x's 95th - 5th percentile) / median norm * 100."""
    import numpy as np
    x = np.asarray(x, dtype=np.float64)
    n = np.asarray(norm, dtype=np.float64)
    p5, p95 = (float(v) for v in np.percentile(x, [5, 95]))
    med = float(np.median(n))
    out = {"pairs": int(len(x)), "x_p5": round(p5, 3), "x_p95": round(p95, 3),
           "x_median": round(float(np.median(x)), 3), "median_ga": round(med, 4)}
    if len(x) < 3 or float(np.std(x)) == 0.0 or float(np.std(n)) == 0.0 or med <= 0:
        out.update({"slope_ga": None, "r": None, "pct": None})
        return out
    b, a = (float(v) for v in np.polyfit(x, n, 1))
    r = float(np.corrcoef(x, n)[0, 1])
    out.update({"slope_ga": round(b, 7), "intercept_ga": round(a, 5), "r": round(r, 4),
                "pct": round(abs(b) * (p95 - p5) / med * 100.0, 3)})
    return out


def classify(c):
    """'strong', 'moderate' or 'weak' coupling, or None without a fit."""
    if not c or c.get("pct") is None or c.get("r") is None:
        return None
    if c["pct"] >= STRONG_PCT and abs(c["r"]) >= STRONG_R:
        return "strong"
    if c["pct"] >= MODERATE_PCT and abs(c["r"]) >= MODERATE_R:
        return "moderate"
    return "weak"


def pick_battery(bats):
    """multi_id of the battery_status instance with the highest median valid current, or None."""
    import numpy as np
    best = None
    for mid in sorted(bats):
        b = bats[mid]
        if "current_a" not in b or "timestamp" not in b:
            continue
        c = np.asarray(b["current_a"], dtype=np.float64)
        ok = np.isfinite(c) & (c >= 0)
        if int(np.sum(ok)) < 2:
            continue
        med = float(np.median(c[ok]))
        if best is None or med > best[0]:
            best = (med, mid)
    return None if best is None else best[1]


def _airborne_median(d, field, land, status):
    import numpy as np
    if not d or field not in d:
        return None
    mask, _ = airborne_mask(d["timestamp"], land, status)
    if mask is None:
        return None
    v = np.asarray(d[field], dtype=np.float64)[mask]
    v = v[np.isfinite(v)]
    return round(float(np.median(v)), 4) if len(v) else None


def measure_data(data):
    """What this census records about one log, from {topic: {multi_id: {field: array}}}
    (no positions)."""
    import numpy as np
    mags = data.get("vehicle_magnetometer") or {}
    out = {"mag_instances": len(mags)}
    mag = mags.get(0)
    if not mag or "magnetometer_ga[0]" not in mag:
        out["read"] = "no vehicle_magnetometer data"
        return out
    tm = np.asarray(mag["timestamp"], dtype=np.int64)
    norm = np.sqrt(sum(np.asarray(mag["magnetometer_ga[%d]" % k], dtype=np.float64) ** 2 for k in range(3)))
    dev = most_common_id(mag.get("device_id"))
    if dev:
        out.update({"device_id": dev, "bus": bus_name(dev), "devtype": "0x%02X" % ((dev >> 16) & 0xFF)})
    out["external"] = external_of(data.get("sensor_mag") or {}, dev)
    ld = (data.get("vehicle_land_detected") or {}).get(0)
    st = (data.get("vehicle_status") or {}).get(0)
    land = (ld["timestamp"], ld["landed"]) if ld and "landed" in ld else None
    status = (st["timestamp"], st["arming_state"]) if st and "arming_state" in st else None
    mask, source = airborne_mask(tm, land, status)
    out["airborne_from"] = source
    out["read"] = "ok"
    if mask is None:
        out["measurable"] = False
        out["why_not"] = "no flight state"
        return out
    dt = float(np.median(np.diff(tm))) / 1e6 if len(tm) > 1 else 0.0
    out["airborne_s"] = round(float(np.sum(mask)) * dt, 1)
    out["mag_test_ratio_median"] = _airborne_median((data.get("estimator_status") or {}).get(0),
                                                    "mag_test_ratio", land, status)
    good = mask & np.isfinite(norm) & (norm > 0)
    # thrust (reported, not graded)
    ts = (data.get("vehicle_thrust_setpoint") or {}).get(0)
    if ts and "xyz[0]" in ts and len(ts["timestamp"]) > 1:
        thr = np.sqrt(sum(np.asarray(ts["xyz[%d]" % k], dtype=np.float64) ** 2 for k in range(3)))
        keep_t, thr_at = paired(tm, ts["timestamp"], thr)
        k = good & keep_t & np.isfinite(thr_at)
        if int(np.sum(k)) >= MIN_PAIRS:
            out["thrust_coupling"] = coupling(thr_at[k], norm[k])
    bats = data.get("battery_status") or {}
    mid = pick_battery(bats)
    if mid is None:
        out["measurable"] = False
        out["why_not"] = "no valid battery current"
        return out
    b = bats[mid]
    cur = np.asarray(b["current_a"], dtype=np.float64)
    ok = np.isfinite(cur) & (cur >= 0)
    keep, cur_at = paired(tm, np.asarray(b["timestamp"], dtype=np.int64)[ok], cur[ok])
    k = good & keep
    out["battery_instance"] = int(mid)
    out["pairs"] = int(np.sum(k))
    if out["pairs"] < MIN_PAIRS:
        out["measurable"] = False
        out["why_not"] = "fewer than %d airborne pairs" % MIN_PAIRS
        return out
    c = coupling(cur_at[k], norm[k])
    out["coupling"] = c
    if c["x_median"] < MIN_CURRENT_A:
        out["measurable"] = False
        out["why_not"] = "median airborne current under %.1f A" % MIN_CURRENT_A
        return out
    if c["x_p95"] - c["x_p5"] < MIN_RANGE_A:
        out["measurable"] = False
        out["why_not"] = "current range under %.0f A" % MIN_RANGE_A
        return out
    if c["pct"] is None:
        out["measurable"] = False
        out["why_not"] = "no fit"
        return out
    out["measurable"] = True
    out["class"] = classify(c)
    return out


def measure_file(path):
    from pyulog import ULog
    u = ULog(path, list(TOPICS), True)
    data = {}
    for d in u.data_list:
        data.setdefault(d.name, {})[d.multi_id] = d.data
    return measure_data(data)


# ---------------------------------------------------------------- the pass

def run(cands, fetch_fn, measure_fn, write, tmp_path, max_heads=500, max_full=150, sleep=time.sleep,
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
            if info.get("has_mag") and info.get("has_battery"):
                rec["status"] = "mag and battery"
            else:
                rec["status"] = "no mag or battery topic"
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


def comp_type(rec):
    v = (rec.get("params") or {}).get("CAL_MAG_COMP_TYP")
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def summarise(recs):
    """The plan's predictions and the reported figures, from every record written."""
    downloaded = [r for r in recs if r.get("measure")]
    read = [r for r in downloaded if r["measure"].get("read") == "ok"]
    meas = [r for r in read if r["measure"].get("measurable")]
    pcts = [r["measure"]["coupling"]["pct"] for r in meas]
    classes = [r["measure"].get("class") for r in meas]
    heads = [r for r in recs if comp_type(r) is not None]
    comp_on = [r for r in heads if comp_type(r) != 0]
    with_ratio = [r for r in meas if r["measure"].get("mag_test_ratio_median") is not None]
    rho = spearman([r["measure"]["coupling"]["pct"] for r in with_ratio],
                   [r["measure"]["mag_test_ratio_median"] for r in with_ratio])
    internal = [r["measure"]["coupling"]["pct"] for r in meas if r["measure"].get("external") is False]
    external = [r["measure"]["coupling"]["pct"] for r in meas if r["measure"].get("external") is True]
    p = {}
    p["K1"] = (len(meas) / len(downloaded) >= K1_SHARE) if downloaded else None
    p["K2"] = (statistics.median(pcts) < K2_PCT) if pcts else None
    p["K3"] = (classes.count("strong") / len(classes) >= K3_SHARE) if classes else None
    p["K4"] = (len(comp_on) / len(heads) < K4_SHARE) if heads else None
    p["K5"] = (rho is not None and rho > K5_RHO) if len(with_ratio) >= K5_MIN else None
    p["K6"] = ((statistics.median(internal) > statistics.median(external))
               if len(internal) >= K6_MIN and len(external) >= K6_MIN else None)
    thrust = [r["measure"]["thrust_coupling"]["pct"] for r in meas
              if (r["measure"].get("thrust_coupling") or {}).get("pct") is not None]
    rep = {
        "requests": len(recs),
        "status_counts": _counts(r.get("status", "") if not str(r.get("status", "")).startswith("error")
                                 else "error" for r in recs),
        "downloaded": len(downloaded), "read_ok": len(read), "measurable": len(meas),
        "read_errors": [r["measure"].get("read") for r in downloaded if r["measure"].get("read") != "ok"],
        "why_not": _counts(r["measure"].get("why_not") for r in read if not r["measure"].get("measurable")),
        "classes": _counts(classes),
        "pct_quantiles": _quantiles(pcts),
        "strong_share": round(classes.count("strong") / len(classes), 4) if classes else None,
        "comp_type_counts": _counts(comp_type(r) for r in heads),
        "comp_on_rows": [{"order": r.get("order"), "comp_type": comp_type(r),
                          "pct": ((r.get("measure") or {}).get("coupling") or {}).get("pct"),
                          "class": (r.get("measure") or {}).get("class")} for r in comp_on],
        "spearman_pct_vs_mag_test_ratio": round(rho, 4) if rho is not None else None,
        "with_mag_test_ratio": len(with_ratio),
        "internal_n": len(internal), "external_n": len(external),
        "internal_pct_median": _med(internal), "external_pct_median": _med(external),
        "external_unknown": sum(1 for r in meas if r["measure"].get("external") is None),
        "pct_median_by_bus": {k: _med([r["measure"]["coupling"]["pct"] for r in meas
                                       if r["measure"].get("bus") == k])
                              for k in sorted({str(r["measure"].get("bus")) for r in meas})},
        "pct_median_by_mav_type": {k: _med([r["measure"]["coupling"]["pct"] for r in meas
                                            if str(r.get("mav_type")) == k])
                                   for k in sorted({str(r.get("mav_type")) for r in meas})},
        "mav_types": _counts(r.get("mav_type") for r in meas),
        "airborne_from": _counts(r["measure"].get("airborne_from") for r in read),
        "thrust_pct_quantiles": _quantiles(thrust),
        "negative_slope_share": (round(sum(1 for r in meas if r["measure"]["coupling"]["slope_ga"] < 0)
                                       / len(meas), 4) if meas else None),
        "strong_rows": sorted(
            [{"order": r.get("order"), "sys_hw": r.get("sys_hw"), "mav_type": r.get("mav_type"),
              "bus": r["measure"].get("bus"), "external": r["measure"].get("external"),
              "pct": r["measure"]["coupling"]["pct"], "r": r["measure"]["coupling"]["r"],
              "slope_mga_per_a": round(r["measure"]["coupling"]["slope_ga"] * 1000.0, 3),
              "comp_type": comp_type(r)}
             for r in meas if r["measure"].get("class") == "strong"], key=lambda x: -x["pct"]),
    }
    return {"mag_logs": __version__, "predictions": p, "reported": rep}


def _counts(xs):
    out = {}
    for x in xs:
        k = str(x)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 3) if xs else None


def _quantiles(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    def q(p):
        return round(xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))], 3)
    return {"n": len(xs), "p10": q(0.10), "p25": q(0.25), "median": round(statistics.median(xs), 3),
            "p75": q(0.75), "p90": q(0.90), "max": xs[-1]}


def main(argv=None):
    import ijson
    import urllib.request
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--plan", default="PLAN.md")
    ap.add_argument("--budget-min", type=float, default=300)
    ap.add_argument("--max-heads", type=int, default=500)
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
        with open(os.path.join(args.out_dir, "mag-summary.json"), "w") as f:
            json.dump({"mag_logs": __version__, "stop": "plan not on main; no log read"}, f, indent=1)
        return 3
    started = time.time()
    req = urllib.request.Request(gn.DBINFO, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as resp:
        cands, counts = build(ijson.items(gn.open_stream(resp), "item"))
    counts["candidates"] = len(cands)
    counts["candidate_vehicles"] = len({c["vehicle"] for c in cands})
    print(json.dumps(counts), flush=True)
    recs = []
    tmp = os.path.join(tempfile.gettempdir(), "mag-log.ulg")
    with open(os.path.join(args.out_dir, "mag-rows.jsonl"), "w", encoding="utf-8") as out:
        def write(rec):
            recs.append(rec)
            out.write(json.dumps(rec, default=gn.plain) + "\n")
            out.flush()
            m = rec.get("measure") or {}
            print("%d %s %s %s %s %s %s %s" % (
                rec["order"], rec["mav_type"], rec["log_id"], rec["ver_sw_release"], rec["status"],
                m.get("measurable"), (m.get("coupling") or {}).get("pct"), m.get("why_not")), flush=True)
        res = run(cands, fetch_one, measure_file, write, tmp, max_heads=args.max_heads,
                  max_full=args.max_full, budget_s=args.budget_min * 60)
    summary = summarise(recs)
    summary.update({"list": counts, "pass": res, "wall_s": round(time.time() - started, 1)})
    text = json.dumps(summary, indent=1, default=gn.plain)
    with open(os.path.join(args.out_dir, "mag-summary.json"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""compass.py: does motor current bend this flight's compass? (px4-log-health compass check)

PX4's docs and Flight Review's 'Thrust and Magnetic Field' plot say the norm of the measured
magnetic field should stay constant in flight. If it tracks the battery current, the current in
the power wiring is bending the compass and the yaw estimate suffers. This check fits, over the
airborne part of one ULog, the norm of vehicle_magnetometer instance 0 (PX4's calibrated and, if
enabled, power-compensated field) against the battery current: norm = a + b * current. The
coupling is |b| times the flight's current range (5th to 95th percentile) as a percentage of the
median norm, and it is placed against 63 measurable public PX4 flights from the pre-registered
census magcurrent-2026 (studies/magcurrent-2026/GRADE.md).

The measurement is the census's own (mag_logs.py 0.1.0), copied unchanged, so a log gets the same
figure here as in the census. It suggests no parameter values: the fix is to move the compass away
from power wiring, or to follow PX4's Compass Power Compensation guide.

    python compass.py flight.ulg [more.ulg ...] [--json]

Needs numpy and pyulog. By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents.
Apache-2.0.
"""

import argparse
import json
import sys

__version__ = "0.1.0"

GUIDE = "https://docs.px4.io/main/en/advanced_config/compass_power_compensation"
TOPICS = ("vehicle_magnetometer", "battery_status", "vehicle_land_detected", "vehicle_status",
          "vehicle_thrust_setpoint")

# one log's measurement (as in the census, mag_logs.py 0.1.0)
MIN_PAIRS = 150             # 30 s of airborne pairs at the default 5 Hz
MIN_RANGE_A = 5.0
MIN_CURRENT_A = 0.5
MAX_GAP_S = 0.5
STRONG_PCT, STRONG_R = 10.0, 0.5
MODERATE_PCT, MODERATE_R = 5.0, 0.3

# coupling % on the 63 measurable flights of magcurrent-2026 (mag-summary.json, pct_quantiles)
FLEET = {"source": "magcurrent-2026: 63 measurable public PX4 flights (Flight Review, read 2026-11-09)",
         "n": 63, "p10": 0.507, "p25": 1.265, "p50": 3.724, "p75": 12.963, "p90": 21.557,
         "strong_share": 0.1746}
CUTS = ("p10", "p25", "p50", "p75", "p90")


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


def measure_data(data):
    """The census's measurement of one log, from {topic: {multi_id: {field: array}}}. As in
    mag_logs.py 0.1.0, without the fields this check does not use (is_external, mag_test_ratio)."""
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
    good = mask & np.isfinite(norm) & (norm > 0)
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


def fleet_place(pct, fleet=FLEET):
    """Where `pct` falls among the census flights: 'below p10', 'p10-p25', ..., 'above p90'."""
    if pct is None:
        return None
    prev = None
    for k in CUTS:
        if pct < fleet[k]:
            return "below p10" if prev is None else "%s-%s" % (prev, k)
        prev = k
    return "above p90"


def verdict(m, comp_type=None):
    """(level, text) for one measurement. level: 'flag', 'note', 'ok' or 'not checked'."""
    if m.get("read") != "ok":
        return "not checked", "Compass not checked: %s." % m.get("read")
    if not m.get("measurable"):
        return "not checked", "Compass not checked: %s." % m.get("why_not")
    c = m["coupling"]
    place = fleet_place(c["pct"])
    what = ("the field norm moves %.1f%% over this flight's current range (%.1f-%.1f A, r = %.2f); "
            "fleet place %s of %d public PX4 flights"
            % (c["pct"], c["x_p5"], c["x_p95"], c["r"], place, FLEET["n"]))
    after = " (after PX4's power compensation, which is on)" if comp_type else ""
    cls = m.get("class")
    if cls == "strong":
        return "flag", ("Compass tracks battery current%s: %s. Motor current is bending the compass, "
                        "which hurts the heading estimate. Move the compass away from power wiring, "
                        "or see PX4's Compass Power Compensation guide: %s" % (after, what, GUIDE))
    if cls == "moderate":
        return "note", ("Compass moves with battery current%s: %s. Worth a look at Flight Review's "
                        "'Thrust and Magnetic Field' plot." % (after, what))
    return "ok", "Compass barely moves with battery current%s: %s." % (after, what)


def read_log(path):
    """({topic: {multi_id: {field: array}}}, CAL_MAG_COMP_TYP or None) from one ULog."""
    from pyulog import ULog
    u = ULog(path, list(TOPICS), True)
    data = {}
    for d in u.data_list:
        data.setdefault(d.name, {})[d.multi_id] = d.data
    comp = u.initial_parameters.get("CAL_MAG_COMP_TYP")
    return data, (None if comp is None else int(comp))


def check(path):
    """The compass check of one ULog: measurement, level, text and fleet place."""
    data, comp = read_log(path)
    m = measure_data(data)
    level, text = verdict(m, comp)
    pct = (m.get("coupling") or {}).get("pct") if m.get("measurable") else None
    return {"compass": __version__, "level": level, "text": text, "coupling_pct": pct,
            "class": m.get("class"), "fleet_place": fleet_place(pct), "cal_mag_comp_typ": comp,
            "measure": m}


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
            r = {"compass": __version__, "level": "not checked",
                 "text": "Compass not checked: %s: %s" % (type(exc).__name__, str(exc)[:200])}
        r["log"] = p
        results.append(r)
        if not args.json:
            print("%s\n  [%s] %s" % (p, r["level"], r["text"]))
    if args.json:
        print(json.dumps(results, indent=1, default=_plain))
    return 0


if __name__ == "__main__":
    sys.exit(main())

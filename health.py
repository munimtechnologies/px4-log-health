"""health.py: how healthy was a PX4 flight, by Flight Review's own limits?

Reads one PX4 flight log (ULog) and reports, for the time the vehicle was in
the air (vehicle_land_detected says not landed):

- vibration: vehicle_imu_status.accel_vibration_metric of the first IMU,
  against the bands Flight Review draws on its vibration plot (green below
  4.905 m/s^2, orange to 9.81, red above). PX4 v1.12 and older computed this
  metric from delta velocities (m/s), about 200 times smaller, so the bands
  are not applied to those logs;
- accelerometer clipping: the increase of vehicle_imu_status.accel_clipping
  (PX4's running total per axis) while flying;
- GNSS: the jamming indicator against the PX4 docs' limits ("should be around
  or below 40", "80 or higher [is] too high"), and the receiver's own jamming
  and spoofing states where it reports them; satellites used and eph. A
  jamming state of 3 (critical) is flagged; 2 (u-blox: interference visible,
  fix kept) is common on ordinary flights and is only noted;
- battery: the highest battery_status.warning (1 low, 2 critical,
  3 emergency, 4 failed) of a battery that reads a voltage;
- failsafe: whether vehicle_status.failsafe was ever set;
- motor saturation (multicopters only): the share of samples in which any
  motor of actuator_motors was commanded at 0.99 or more.

    python health.py flight.ulg [more.ulg ...]

If baseline.json (fleet quantiles from studies/fleet-health-2026) sits next to
this file, each figure is also placed against the fleet, e.g. "p75-p90", or
"p10-p75 (tied)" when most flights share the value (zero clipping, say).

Uses no position, so no location leaves the log. These are the same limits a
person reading Flight Review applies by eye; a flag is a reason to look, not a
diagnosis.

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

__version__ = "0.2.1"

VIB_ORANGE = 4.905      # m/s^2: Flight Review's vibration plot, green below
VIB_RED = 9.81          # m/s^2: red above
JAM_OK = 40             # PX4 docs: jamming indicator "should be around or below 40"
JAM_HIGH = 80           # "Values around 80 or higher are too high"
JAM_STATE_FLAG = 3      # critical; 2 is noted, not flagged (fleet-health-2026 GRADE)
SAT_LEVEL = 0.99        # a motor command at or above this counts as saturated
SAT_SHARE = 0.05        # flagged when that holds in 5% of in-flight samples
MIN_FLIGHT_S = 60.0     # a log needs this much time in the air to be read
MIN_SAMPLES = 10        # in-flight samples a topic needs for its figure
MIN_VOLTAGE = 1.0       # V: a battery reading less is not monitored

TOPICS = ("vehicle_land_detected", "vehicle_imu_status", "vehicle_gps_position",
          "sensor_gps", "battery_status", "vehicle_status", "actuator_motors")
STATE_NAMES = {0: "unknown", 1: "ok", 2: "warning", 3: "critical"}
PLACED = ("flight_s", "vib_median", "vib_p95", "clip_per_min", "jam_median", "jam_p95",
          "sats_median", "eph_median", "sat_share")
BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline.json")


def release_name(value):
    """'v1.14.3' from ULog's ver_sw_release integer (0xAABBCCTT), else None
    (as px4-gps-lag's gps_lag.py names it)."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    name = "v%d.%d.%d" % ((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF)
    kind = v & 0xFF
    return name if kind == 255 else "%s-%d" % (name, kind)


def vib_units(release):
    """'m/s^2' for PX4 v1.13 and newer, 'm/s' for v1.12 and older (VehicleIMU
    then took the metric from delta velocities), None when the release is
    unknown or not a PX4 v1 number (forks, untagged builds)."""
    try:
        major, minor = (int(p) for p in str(release).lstrip("v").split("-")[0].split(".")[:2])
    except (TypeError, ValueError):
        return None
    if major != 1:
        return None
    return "m/s^2" if minor >= 13 else "m/s"


def read_health(path):
    """{'topics': {name: {instance: {field: array}}}, 'release'} from a ULog file."""
    from pyulog import ULog
    ulog = ULog(path, list(TOPICS), True)
    topics = {}
    for d in ulog.data_list:
        topics.setdefault(d.name, {})[d.multi_id] = d.data
    return {"topics": topics, "release": release_name(ulog.msg_info_dict.get("ver_sw_release"))}


def _times(d):
    return np.asarray(d["timestamp"], dtype=float) / 1e6


def flying_at(t, land_t, landed):
    """True where the last vehicle_land_detected message before t says
    'not landed' (False before the first message)."""
    t = np.asarray(t, dtype=float)
    if land_t is None or len(land_t) == 0:
        return np.zeros(len(t), dtype=bool)
    idx = np.searchsorted(np.asarray(land_t, dtype=float), t, side="right") - 1
    state = np.asarray(landed).astype(int)
    out = np.zeros(len(t), dtype=bool)
    ok = idx >= 0
    out[ok] = state[idx[ok]] == 0
    return out


def flight_seconds(land_t, landed, t_end):
    """Seconds spent not landed, each land message holding until the next
    one (the last until t_end)."""
    land_t = np.asarray(land_t, dtype=float)
    if len(land_t) == 0:
        return 0.0
    ends = np.append(land_t[1:], max(float(t_end), float(land_t[-1])))
    flying = np.asarray(landed).astype(int) == 0
    return float(np.sum((ends - land_t)[flying]))


def vibration(imu, fly):
    """Median and 95th percentile of the in-flight vibration metric."""
    if "accel_vibration_metric" not in imu:
        return None
    v = np.asarray(imu["accel_vibration_metric"], dtype=float)[fly]
    v = v[np.isfinite(v)]
    if len(v) < MIN_SAMPLES:
        return None
    return {"median": float(np.median(v)), "p95": float(np.percentile(v, 95))}


def vib_band(value):
    if value is None:
        return None
    return "green" if value < VIB_ORANGE else ("orange" if value <= VIB_RED else "red")


def clipping(imu, fly):
    """Accelerometer clipping events added while flying (all axes), from
    PX4's running totals: the sum of positive steps between consecutive
    samples that are both in flight."""
    if "accel_clipping[0]" not in imu:
        return None
    c = np.column_stack([np.asarray(imu["accel_clipping[%d]" % k], dtype=float)
                         for k in range(3)])
    if len(c) < 2:
        return None
    d = np.diff(c, axis=0)
    both = fly[1:] & fly[:-1]
    d = np.where(d > 0, d, 0.0)
    return int(np.sum(d[both]))


def gnss(g, fly):
    """Jamming, spoofing, satellites and eph of one GPS topic in flight."""
    out = {}
    nfly = int(np.sum(fly))
    if "jamming_indicator" in g:
        j = np.asarray(g["jamming_indicator"], dtype=float)
        out["jam_reported"] = bool(np.any(j != 0))
        jf = j[fly]
        if out["jam_reported"] and len(jf) >= MIN_SAMPLES:
            out["jam_median"] = float(np.median(jf))
            out["jam_p95"] = float(np.percentile(jf, 95))
    for field, key in (("jamming_state", "jam_state"), ("spoofing_state", "spoof_state")):
        if field in g:
            s = np.asarray(g[field]).astype(int)
            out[key + "_reported"] = bool(np.any(s != 0))
            out[key + "_max"] = int(s[fly].max()) if nfly else None
    for field, key in (("satellites_used", "sats_median"), ("eph", "eph_median")):
        if field in g and nfly >= MIN_SAMPLES:
            x = np.asarray(g[field], dtype=float)[fly]
            x = x[np.isfinite(x)]
            if len(x):
                out[key] = float(np.median(x))
    return out


def battery(b, fly):
    """Highest in-flight warning (0-4) of a battery that reads a voltage."""
    if "warning" not in b:
        return None
    w = np.asarray(b["warning"]).astype(int)
    keep = fly.copy()
    if "connected" in b:
        keep &= np.asarray(b["connected"]).astype(bool)
    if "voltage_v" in b:
        v = np.asarray(b["voltage_v"], dtype=float)
        if not np.any(keep) or np.median(v[keep]) < MIN_VOLTAGE:
            return None
    w = w[keep]
    w = w[(w >= 0) & (w <= 4)]
    if len(w) < MIN_SAMPLES:
        return None
    return int(w.max())


def is_multicopter(status):
    """True if vehicle_status says rotary wing and not VTOL."""
    if "vehicle_type" not in status or len(status["vehicle_type"]) == 0:
        return None
    vt = np.asarray(status["vehicle_type"]).astype(int)
    vtol = bool(np.any(np.asarray(status["is_vtol"]).astype(bool))) if "is_vtol" in status else False
    return bool(np.all(vt == 1)) and not vtol


def saturation(motors, fly):
    """(active motors, share of in-flight samples with any active motor at
    SAT_LEVEL or more). A motor is active if its command is a number in 90%
    of in-flight samples."""
    cols = []
    k = 0
    while "control[%d]" % k in motors:
        cols.append(np.asarray(motors["control[%d]" % k], dtype=float)[fly])
        k += 1
    if not cols or int(np.sum(fly)) < MIN_SAMPLES:
        return None
    c = np.column_stack(cols)
    active = np.mean(np.isfinite(c), axis=0) >= 0.9
    if np.sum(active) == 0:
        return 0, None
    a = np.where(np.isfinite(c[:, active]), c[:, active], 0.0)
    return int(np.sum(active)), float(np.mean(np.max(a, axis=1) >= SAT_LEVEL))


def _first(topics, *names):
    """Instance 0 of the first topic present, with its name."""
    for name in names:
        inst = topics.get(name) or {}
        if 0 in inst:
            return name, inst[0]
    return None, None


def measure_health(log):
    """The health of one log (a dict as read_health returns)."""
    topics = log["topics"]
    land = (topics.get("vehicle_land_detected") or {}).get(0)
    if land is None or "landed" not in land:
        return {"health_ok": False, "health_reason": "no vehicle_land_detected"}
    land_t = _times(land)
    landed = np.asarray(land["landed"])
    t_end = max(float(_times(d)[-1]) for inst in topics.values() for d in inst.values()
                if "timestamp" in d and len(d["timestamp"]))
    out = {"flight_s": round(flight_seconds(land_t, landed, t_end), 1), "release": log.get("release")}
    if out["flight_s"] < MIN_FLIGHT_S:
        out.update({"health_ok": False, "health_reason": "under %d s in the air" % MIN_FLIGHT_S})
        return out

    def fly(d):
        return flying_at(_times(d), land_t, landed)

    units = vib_units(out["release"])
    imus = topics.get("vehicle_imu_status") or {}
    out["imu_count"] = len(imus)
    if 0 in imus:
        imu = imus[0]
        f = fly(imu)
        vib = vibration(imu, f)
        if vib:
            out["vib_median"] = round(vib["median"], 3)
            out["vib_p95"] = round(vib["p95"], 3)
            out["vib_units"] = units or "m/s^2 (assumed: release unknown)"
            out["vib_band"] = None if units == "m/s" else vib_band(vib["median"])
        clip = clipping(imu, f)
        if clip is not None:
            out["clip_count"] = clip
            out["clip_per_min"] = round(clip / (out["flight_s"] / 60.0), 3)
    worst = [vibration(d, fly(d)) for d in imus.values()]
    worst = [v["median"] for v in worst if v]
    if worst:
        out["vib_median_worst_imu"] = round(max(worst), 3)

    gname, g = _first(topics, "vehicle_gps_position", "sensor_gps")
    if g is not None:
        out["gnss_topic"] = gname
        out.update(gnss(g, fly(g)))

    b = (topics.get("battery_status") or {}).get(0)
    if b is not None:
        w = battery(b, fly(b))
        if w is not None:
            out["battery_warning_max"] = w

    status = (topics.get("vehicle_status") or {}).get(0)
    if status is not None:
        if "failsafe" in status:
            f = fly(status)
            if np.any(f):
                out["failsafe_in_flight"] = bool(np.any(np.asarray(status["failsafe"]).astype(bool)[f]))
        out["multicopter"] = is_multicopter(status)

    motors = (topics.get("actuator_motors") or {}).get(0)
    if motors is not None and out.get("multicopter"):
        sat = saturation(motors, fly(motors))
        if sat is not None and sat[0] >= 4 and sat[1] is not None:
            out["motors"] = sat[0]
            out["sat_share"] = round(sat[1], 4)

    out["flags"] = flags(out)
    out["notes"] = notes(out)
    out["health_ok"] = True
    out["health_reason"] = "ok"
    return out


def flags(row):
    """Plain-language flags for one measured log."""
    f = []
    if row.get("vib_band") in ("orange", "red"):
        f.append("vibration %s (median %.1f m/s^2; Flight Review's plot turns orange at 4.9 and red at 9.8)"
                 % (row["vib_band"], row["vib_median"]))
    if row.get("clip_count"):
        f.append("accelerometer clipped %d times in flight" % row["clip_count"])
    if row.get("jam_median") is not None and row["jam_median"] > JAM_OK:
        f.append("GPS jamming indicator median %.0f (PX4 docs: should be around or below 40)" % row["jam_median"])
    elif row.get("jam_p95") is not None and row["jam_p95"] >= JAM_HIGH:
        f.append("GPS jamming indicator reached %.0f (PX4 docs: 80 or higher is too high)" % row["jam_p95"])
    if (row.get("jam_state_max") or 0) >= JAM_STATE_FLAG:
        f.append("GPS receiver reported jamming (%s)" % STATE_NAMES.get(row["jam_state_max"], row["jam_state_max"]))
    if (row.get("spoof_state_max") or 0) >= 2:
        f.append("GPS receiver reported spoofing (state %d)" % row["spoof_state_max"])
    if (row.get("battery_warning_max") or 0) >= 1:
        f.append("battery warning level %d in flight" % row["battery_warning_max"])
    if row.get("failsafe_in_flight"):
        f.append("failsafe in flight")
    if row.get("sat_share") is not None and row["sat_share"] >= SAT_SHARE:
        f.append("a motor at full command in %.0f%% of flight" % (100 * row["sat_share"]))
    return f


def notes(row):
    """Things worth knowing that are not flags."""
    n = []
    if row.get("vib_units") == "m/s":
        n.append("vibration not banded: PX4 %s logs the metric in m/s (from delta velocities), "
                 "so Flight Review's m/s^2 bands do not apply" % row.get("release"))
    if row.get("jam_state_max") == 2:
        n.append("GPS receiver reported interference (jamming state 2: visible, fix kept); "
                 "common on ordinary flights (70% of public logs that report a state)")
    return n


def load_baseline(path=BASELINE):
    """The fleet quantiles of studies/fleet-health-2026, or None."""
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def place(value, q):
    """Where value sits among the quantiles q ({'p10': .., ..., 'p99': ..}):
    'below p10', 'p25-p50', 'above p99' and so on. When two or more cuts
    equal the value, most flights share it (zero clipping, say), and the
    whole tied range is named: 'p10-p75 (tied)'."""
    if value is None or not q:
        return None
    cuts = [(k, q[k]) for k in ("p10", "p25", "p50", "p75", "p90", "p99") if q.get(k) is not None]
    if not cuts:
        return None
    tied = [k for k, c in cuts if c == value]
    if len(tied) >= 2:
        return "%s-%s (tied)" % (tied[0], tied[-1])
    if value < cuts[0][1]:
        return "below " + cuts[0][0]
    for (lo, a), (hi, b) in zip(cuts, cuts[1:]):
        if a <= value < b:
            return "%s-%s" % (lo, hi)
    return "above " + cuts[-1][0]


def fleet_place(row, baseline):
    """Each figure of a measured log against the fleet quantiles."""
    if not baseline or not row.get("health_ok"):
        return None
    fields = baseline.get("fields") or {}
    out = {}
    for k in PLACED:
        if k in ("vib_median", "vib_p95") and row.get("vib_units") == "m/s":
            continue
        p = place(row.get(k), fields.get(k))
        if p:
            out[k] = p
    return out


def main(argv=None):
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        print(__doc__)
        return 2
    base = load_baseline()
    for path in paths:
        try:
            r = measure_health(read_health(path))
        except Exception as exc:  # report and go on to the next log
            r = {"health_ok": False, "health_reason": "error: %s" % exc}
        if base:
            r["fleet_place"] = fleet_place(r, base)
        r["log"] = path
        print(json.dumps(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

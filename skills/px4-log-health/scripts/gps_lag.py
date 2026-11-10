"""gps_lag.py: how far behind the IMU is a PX4 vehicle's GPS velocity?

PX4's EKF2 fuses each GPS sample as if it were measured at
vehicle_gps_position.timestamp minus EKF2_GPS_DELAY (110 ms by default; the
timestamp is when the flight controller published the sample). This measures
that delay from a flight log. For each pair of consecutive logged GPS samples
taken in flight, the change in GPS north and east velocity is compared with the
IMU's specific force, rotated to north-east with the logged attitude and
integrated over the same interval shifted back by a candidate lag. The lag with
the smallest squared error (with one constant acceleration bias per axis) is
the measured delay, with a nominal standard error from the curvature of the
error around it.

    python gps_lag.py flight.ulg [more.ulg ...]

Reads sensor_combined, vehicle_attitude, vehicle_gps_position (or, on PX4
main since PR #24399, vehicle_gnss; new in 0.2.0) and vehicle_land_detected
with pyulog. Uses no position, so no location leaves the
log. A measured lag is a suggestion to check with EKF2 replay, not a setting.

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import json
import sys

import numpy as np

__version__ = "0.2.0"

LAG_MIN_S = -0.2
LAG_MAX_S = 0.8
LAG_STEP_S = 0.005
MAX_PAIR_DT_S = 1.2     # consecutive logged GPS samples further apart are not paired
MIN_PAIR_DT_S = 0.05
MAX_IMU_GAP_S = 0.1     # a pair whose IMU window has a longer gap is dropped
MIN_FIX = 3             # 3D fix or better

# A log counts only if all of these hold.
MIN_PAIRS = 100
MIN_EXCITATION = 0.3    # m/s^2: RMS of the GPS velocity changes over their intervals, about the mean
MIN_R2 = 0.6
MAX_SE_S = 0.02

TOPICS = ("sensor_combined", "vehicle_attitude", "vehicle_gps_position",
          "vehicle_land_detected", "vehicle_gnss")
# PX4 main (PR #24399, 2026-09-29) logs vehicle_gnss instead of vehicle_gps_position. The selected
# receiver's sensor_gnss sample is nested as 'receiver' (pyulog: 'receiver.<field>'), its velocity
# is vel_north / vel_east, and the delay-corrected sample time is the top-level timestamp_sample.
# For each vehicle_gps_position column read_log reads: where vehicle_gnss has it, first match wins.
GNSS_COLUMNS = (("timestamp", ("receiver.timestamp", "timestamp")),
                ("vel_n_m_s", ("receiver.vel_north", "receiver.vel_n_m_s")),
                ("vel_e_m_s", ("receiver.vel_east", "receiver.vel_e_m_s")),
                ("fix_type", ("receiver.fix_type",)),
                ("vel_ned_valid", ("receiver.vel_ned_valid",)),
                ("timestamp_sample", ("timestamp_sample", "receiver.timestamp_sample")),
                ("device_id", ("receiver.device_id",)))
# PX4 DeviceBusType, the low 3 bits of a device_id
BUS_TYPES = {0: "unknown", 1: "i2c", 2: "spi", 3: "uavcan", 4: "simulation",
             5: "serial", 6: "mavlink"}
RELATIVE_TIMESTAMP_INVALID = 0x7FFFFFFF


def lag_grid(lag_min=LAG_MIN_S, lag_max=LAG_MAX_S, step=LAG_STEP_S):
    n = int(round((lag_max - lag_min) / step))
    return lag_min + step * np.arange(n + 1)


def bus_type(device_id):
    """Bus of a PX4 device_id: 'serial', 'uavcan', ... ('unknown' for 0)."""
    try:
        return BUS_TYPES.get(int(device_id) & 0x7, "unknown")
    except (TypeError, ValueError):
        return "unknown"


def align_quaternions(q):
    """q (n, 4) with each row on the same side as the one before it, so that
    component-wise interpolation never passes near zero."""
    q = np.array(q, dtype=float)
    if len(q) < 2:
        return q
    dots = np.sum(q[1:] * q[:-1], axis=1)
    sign = np.concatenate([[1.0], np.cumprod(np.where(dots < 0, -1.0, 1.0))])
    return q * sign[:, None]


def ne_accel(imu_t, f_body, att_t, q):
    """North and east acceleration (m/s^2) at imu_t from body-frame specific
    force f_body (n, 3) and PX4's body-to-NED attitude quaternions q (m, 4),
    (w, x, y, z), logged at att_t and interpolated to imu_t. Gravity acts only
    on the down axis, so it does not enter."""
    q = align_quaternions(q)
    qi = np.column_stack([np.interp(imu_t, att_t, q[:, k]) for k in range(4)])
    qi /= np.linalg.norm(qi, axis=1)[:, None]
    w, x, y, z = qi.T
    f = np.asarray(f_body, dtype=float)
    fx, fy, fz = f[:, 0], f[:, 1], f[:, 2]
    an = (1 - 2 * (y * y + z * z)) * fx + 2 * (x * y - w * z) * fy + 2 * (x * z + w * y) * fz
    ae = 2 * (x * y + w * z) * fx + (1 - 2 * (x * x + z * z)) * fy + 2 * (y * z - w * x) * fz
    return np.column_stack([an, ae])


def cumulative(imu_t, a):
    """Running integral of a (n, k) at imu_t. Each sample is taken as the mean
    over the interval since the sample before it, which is what PX4's
    sensor_combined reports."""
    dt = np.diff(np.asarray(imu_t, dtype=float), prepend=imu_t[0])
    return np.cumsum(np.asarray(a, dtype=float) * dt[:, None], axis=0)


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


def gps_pairs(gps_t, usable, imu_t, lag_min=LAG_MIN_S, lag_max=LAG_MAX_S):
    """Indices i of the pairs (i, i + 1) of consecutive logged GPS samples that
    are both usable, 0.05-1.2 s apart, and whose IMU window
    [t_i - lag_max, t_(i+1) - lag_min] is inside the IMU record with no gap
    over 0.1 s."""
    gps_t = np.asarray(gps_t, dtype=float)
    usable = np.asarray(usable, dtype=bool)
    imu_t = np.asarray(imu_t, dtype=float)
    if len(gps_t) < 2 or len(imu_t) < 2:
        return np.zeros(0, dtype=int)
    i = np.arange(len(gps_t) - 1)
    dt = gps_t[1:] - gps_t[:-1]
    keep = usable[:-1] & usable[1:] & (dt >= MIN_PAIR_DT_S) & (dt <= MAX_PAIR_DT_S)
    ws = gps_t[:-1] - lag_max
    we = gps_t[1:] - lag_min
    keep &= (ws >= imu_t[0]) & (we <= imu_t[-1])
    gaps = np.nonzero(np.diff(imu_t) > MAX_IMU_GAP_S)[0]
    if len(gaps):
        g_start = imu_t[gaps]
        g_end = imu_t[gaps + 1]
        overlapping = (np.searchsorted(g_start, we, side="left")
                       - np.searchsorted(g_end, ws, side="right"))
        keep &= overlapping <= 0
    return i[keep]


def fit_lag(imu_t, a_ne, t1, t2, dv, lags=None):
    """The lag (s) that best explains the GPS velocity changes dv (n, 2) over
    [t1, t2] by the IMU's north-east acceleration a_ne integrated over
    [t1 - lag, t2 - lag], with one constant bias per axis. Returns a dict."""
    lags = lag_grid() if lags is None else np.asarray(lags, dtype=float)
    imu_t = np.asarray(imu_t, dtype=float)
    V = cumulative(imu_t, a_ne)
    t1 = np.asarray(t1, dtype=float)
    t2 = np.asarray(t2, dtype=float)
    dv = np.asarray(dv, dtype=float)
    d = t2 - t1
    dd = float(np.sum(d * d))
    n = len(d)
    sse = np.empty(len(lags))
    biases = np.empty((len(lags), 2))
    for j, lag in enumerate(lags):
        X = np.column_stack([np.interp(t2 - lag, imu_t, V[:, k]) - np.interp(t1 - lag, imu_t, V[:, k])
                             for k in (0, 1)])
        r = dv - X
        b = np.sum(r * d[:, None], axis=0) / dd
        e = r - d[:, None] * b
        sse[j] = float(np.sum(e * e))
        biases[j] = b
    best = int(np.argmin(sse))
    lag = float(lags[best])
    edge = best == 0 or best == len(lags) - 1
    se = float("inf")
    if not edge:
        h = float(lags[best + 1] - lags[best])
        s0, s1, s2 = sse[best - 1], sse[best], sse[best + 1]
        denom = s0 - 2 * s1 + s2
        if denom > 0:
            lag = lag + 0.5 * h * (s0 - s2) / denom
            curvature = denom / (2 * h * h)          # SSE ~ min + curvature * (L - L*)^2
            sigma2 = s1 / max(2 * n - 3, 1)
            se = float(np.sqrt(sigma2 / curvature))
    sst = float(np.sum((dv - dv.mean(axis=0)) ** 2))
    acc = dv / d[:, None]
    return {
        "lag_s": lag,
        "se_s": se,
        "edge": bool(edge),
        "pairs": int(n),
        "excitation_m_s2": float(np.sqrt(np.sum((acc - acc.mean(axis=0)) ** 2) / max(2 * n, 1))),
        "r2": (1.0 - float(sse[best]) / sst) if sst > 0 else None,
        "r2_at_zero": ((1.0 - float(sse[int(np.argmin(np.abs(lags)))]) / sst) if sst > 0 else None),
        "bias_ne_m_s2": [float(biases[best, 0]), float(biases[best, 1])],
    }


def passes(fit):
    """The gates a log must pass to count (see the module constants)."""
    if fit.get("pairs", 0) < MIN_PAIRS:
        return False, "too few in-flight GPS pairs"
    if fit["excitation_m_s2"] < MIN_EXCITATION:
        return False, "too little manoeuvring"
    if fit["edge"]:
        return False, "best lag at the edge of the search"
    if fit["r2"] is None or fit["r2"] < MIN_R2:
        return False, "IMU explains too little of the GPS velocity change"
    if not np.isfinite(fit["se_s"]) or fit["se_s"] > MAX_SE_S:
        return False, "lag too uncertain"
    return True, "ok"


def _col(d, name, default=None):
    return d[name] if name in d else default


def gnss_as_gps(g):
    """The vehicle_gps_position columns read_log reads, from a vehicle_gnss topic (PX4 main).
    A column vehicle_gnss doesn't have is left out, so read_log names what is missing."""
    out = {}
    for name, sources in GNSS_COLUMNS:
        src = next((s for s in sources if s in g), None)
        if src is not None:
            out[name] = g[src]
    return out


def read_log(path):
    """The arrays measure() needs, from a ULog file, plus a few facts about the
    vehicle. Raises ValueError when a topic or field is missing."""
    from pyulog import ULog
    ulog = ULog(path, list(TOPICS), True)
    data = {}
    for d in ulog.data_list:
        if d.name in TOPICS and d.multi_id == 0:
            data[d.name] = d.data
    if "vehicle_gps_position" not in data and "vehicle_gnss" in data:
        data["vehicle_gps_position"] = gnss_as_gps(data["vehicle_gnss"])
    for name in ("sensor_combined", "vehicle_attitude", "vehicle_gps_position"):
        if name not in data:
            raise ValueError("no %s in the log" % name)
    sc = data["sensor_combined"]
    if "accelerometer_m_s2[0]" not in sc:
        raise ValueError("sensor_combined has no accelerometer_m_s2")
    t = sc["timestamp"].astype(np.int64)
    rel = _col(sc, "accelerometer_timestamp_relative")
    if rel is not None:
        rel = rel.astype(np.int64)
        good = rel != RELATIVE_TIMESTAMP_INVALID
        t = t + np.where(good, rel, 0)
    else:
        good = np.ones(len(t), dtype=bool)
    f = np.column_stack([sc["accelerometer_m_s2[%d]" % k] for k in range(3)]).astype(float)
    imu_t = t[good] / 1e6
    f = f[good]
    order = np.argsort(imu_t, kind="stable")
    imu_t, f = imu_t[order], f[order]
    inc = np.concatenate([[True], np.diff(imu_t) > 0])
    imu_t, f = imu_t[inc], f[inc]

    att = data["vehicle_attitude"]
    att_t = att["timestamp"] / 1e6
    q = np.column_stack([att["q[%d]" % k] for k in range(4)]).astype(float)

    g = data["vehicle_gps_position"]
    for field in ("timestamp", "vel_n_m_s", "vel_e_m_s", "fix_type"):
        if field not in g:
            raise ValueError("vehicle_gps_position has no %s" % field)
    gps_t = g["timestamp"] / 1e6
    vel = np.column_stack([g["vel_n_m_s"], g["vel_e_m_s"]]).astype(float)
    usable = (g["fix_type"] >= MIN_FIX) & np.all(np.isfinite(vel), axis=1)
    if "vel_ned_valid" in g:
        usable &= g["vel_ned_valid"].astype(bool)
    order = np.argsort(gps_t, kind="stable")
    gps_t, vel, usable = gps_t[order], vel[order], usable[order]
    stamp_minus_sample_ms = None
    if "timestamp_sample" in g:
        ts = g["timestamp_sample"].astype(np.int64)
        tt = g["timestamp"].astype(np.int64)
        nz = ts > 0
        if np.any(nz):
            stamp_minus_sample_ms = float(np.median(tt[nz] - ts[nz]) / 1e3)

    land = data.get("vehicle_land_detected")
    land_t = land["timestamp"] / 1e6 if land is not None else None
    landed = land["landed"] if land is not None and "landed" in land else None
    if landed is None:
        land_t = None

    device_id = int(g["device_id"][len(g["device_id"]) // 2]) if "device_id" in g else 0
    params = ulog.initial_parameters
    info = ulog.msg_info_dict
    release = info.get("ver_sw_release")
    return {
        "imu_t": imu_t, "f_body": f, "att_t": att_t, "q": q,
        "gps_t": gps_t, "vel_ne": vel, "usable": usable,
        "land_t": land_t, "landed": landed,
        "facts": {
            "param_gps_delay_ms": (float(params["EKF2_GPS_DELAY"])
                                   if "EKF2_GPS_DELAY" in params else None),
            "sdlog_profile": (int(params["SDLOG_PROFILE"]) if "SDLOG_PROFILE" in params else None),
            "gps_bus": bus_type(device_id),
            "gps_logged_dt_s": (float(np.median(np.diff(gps_t))) if len(gps_t) > 1 else None),
            "stamp_minus_sample_ms": stamp_minus_sample_ms,
            "ver_hw": info.get("ver_hw"),
            "ver_sw_release": release_name(release),
        },
    }


def release_name(value):
    """'v1.14.3' from ULog's ver_sw_release integer (0xAABBCCTT), else None."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    name = "v%d.%d.%d" % ((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF)
    kind = v & 0xFF
    return name if kind == 255 else "%s-%d" % (name, kind)


def measure(log):
    """Measure the GPS lag of one log (a dict as read_log returns)."""
    flying = flying_at(log["gps_t"], log["land_t"], log["landed"])
    usable = np.asarray(log["usable"], dtype=bool) & flying
    idx = gps_pairs(log["gps_t"], usable, log["imu_t"])
    out = dict(log.get("facts", {}))
    out["in_flight_gps_samples"] = int(np.sum(usable))
    if len(idx) < 3:
        out.update({"pairs": int(len(idx)), "passed": False, "reason": "too few in-flight GPS pairs"})
        return out
    a_ne = ne_accel(log["imu_t"], log["f_body"], log["att_t"], log["q"])
    gps_t = np.asarray(log["gps_t"], dtype=float)
    vel = np.asarray(log["vel_ne"], dtype=float)
    fit = fit_lag(log["imu_t"], a_ne, gps_t[idx], gps_t[idx + 1], vel[idx + 1] - vel[idx])
    ok, reason = passes(fit)
    out.update(fit)
    if not np.isfinite(fit["se_s"]):
        out["se_s"] = None      # keep the output strict JSON
    out["lag_ms"] = round(fit["lag_s"] * 1e3, 1)
    out["se_ms"] = round(fit["se_s"] * 1e3, 2) if np.isfinite(fit["se_s"]) else None
    out["passed"] = ok
    out["reason"] = reason
    if out.get("param_gps_delay_ms") is not None:
        out["lag_minus_param_ms"] = round(out["lag_ms"] - out["param_gps_delay_ms"], 1)
    return out


def main(argv=None):
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        print(__doc__)
        return 2
    for path in paths:
        try:
            r = measure(read_log(path))
        except Exception as exc:  # report and go on to the next log
            r = {"passed": False, "reason": "error: %s" % exc}
        r["log"] = path
        print(json.dumps(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

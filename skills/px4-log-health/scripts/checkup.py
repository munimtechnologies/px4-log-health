"""checkup.py: one readable report per PX4 flight log.

Runs health.py on each log and places every figure against 208 public
flights (baseline.json). When px4-gps-lag's gps_lag.py and innov.py sit next
to this file (fetch_deps.py or the GitHub Action puts them there), it also
measures how far the GPS lags the IMU, compares that with the delay EKF2
assumed, and checks the direction against EKF2's own GPS velocity
innovations (px4-gps-lag, studies/fleet-2026/GRADE-innov-confirm.md). When
the lag is more than 50 ms off, it says what px4-gps-lag's mismatch-2026
study saw on public logs that far off (studies/mismatch-2026/GRADE.md).
When a serial u-blox receiver runs GPS_UBX_DYNMODEL 6 (airborne <1g), it
says what px4-gps-lag's dynmodel-2026 study, its paired check and its
mechanism checks saw (studies/dynmodel-2026/GRADE.md, GRADE-paired.md,
GRADE-mechanism.md and GRADE-tau-fleet.md).

The delay EKF2 assumed is the log's EKF2_GPS_DELAY up to PX4 v1.17. PX4 PR
#26660 (on main since 2026-03-18, in v1.18) removed that parameter: the
sensors module now moves each sample's timestamp_sample back by a delay per
receiver, SENS_GPS0_DELAY or SENS_GPS1_DELAY (renamed SENS_GNSS0_DELAY /
SENS_GNSS1_DELAY on main by PR #24399, 2026-09-29), or by a PPS correction,
and EKF2 fuses the sample at that time. On those logs the delay is read from
the log itself, as timestamp minus timestamp_sample. A GPS driver that
stamps its own sample times overrides the delay parameter; DroneCAN receivers
do since PR #27427 (v1.18 and main), and the report then says so (0.3.7).

When compass.py sits next to this file (it is in the repo and the skill), the
report ends with a Compass section (0.3.8): how far the field norm of the
compass the estimator used moves with battery current in flight, placed
against 63 measurable public flights of the magcurrent-2026 census
(studies/magcurrent-2026/GRADE.md). Strong coupling is a flag.

When battery_r.py sits next to this file (it is in the repo and the skill),
a Battery section follows (0.3.9): whether PX4's online battery-resistance
estimate collapsed to 0 in flight, which leaves the state of charge
uncorrected for load when BAT1_R_INTERNAL is -1. It comes from the
battery-r-2026 census (studies/battery-r-2026/GRADE.md). A collapse is a
note, not a flag, and no resistance value is suggested.

    python checkup.py flight.ulg [more.ulg ...] [--md report.md] [--json rows.jsonl] [--fail-on-flag]

Markdown goes to --md (appended, so it can be $GITHUB_STEP_SUMMARY) or to
stdout; one JSON line per log goes to --json. With --fail-on-flag the exit
code is 1 if any log raised a flag. Reads no position, so no location
leaves the log. A flag is a reason to look at the log in Flight Review, and
a suggested GPS delay is a starting value to check with EKF2 log replay,
not a setting to fly.

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

import health

__version__ = "0.3.9"

OFF_MS = 50.0           # px4-gps-lag rule D1: suggest only when the lag is this far off the delay EKF2 assumed
NEAR_MS = 40.0          # within OFF_MS but this close to it: say so, with one decimal
DELAY_TOL_MS = 1.0      # an applied delay this close to a SENS_*_DELAY came from that parameter
DEFAULT_DELAY_MS = 110.0  # PX4 v1.18+: used when SENS_GPSn_ID are set and none matches the receiver
DELAY_TOPICS = ("vehicle_gps_position", "vehicle_gnss")
NEW_TOPIC_REASON = ("this log uses PX4's newer vehicle_gnss topic (PX4 main since 2026-09-29), "
                    "which this copy of gps_lag.py does not read (px4-gps-lag 0.2.0 does; run fetch_deps.py)")
DRONECAN_BUS = 3  # bits 0-2 of a PX4 device id are the bus type; 3 is UAVCAN (DroneCAN)
DRONECAN_NOTE = ("this DroneCAN receiver's own time stamps set its sample times (PX4 PR #27427, in v1.18 "
                 "and main), and PX4 then does not apply %sn_DELAY, so changing that parameter would not "
                 "change when EKF2 fuses its samples")
# px4-gps-lag studies/mismatch-2026/GRADE.md (pre-registered; D1 fired, M3 failed): 256 public logs,
# 93 more than 50 ms off their setting. Median lift (log10 of the GPS velocity test ratio while
# accelerating over steady flight) 0.4348 vs 0.24285; logs with any rejection 20.43% vs 15.95%,
# difference +4.5 points, 95% interval -5.3 to +14.2.
MISMATCH_NOTE = ("On public PX4 logs, a delay this far off went with higher EKF2 GPS velocity test ratios "
                 "while accelerating (median lift 0.43 vs 0.24: the ratio while accelerating was 2.7 times "
                 "its steady-flight value, against 1.7 times on logs within 50 ms), and mismatched logs saw "
                 "more GPS velocity rejections (20% of logs against 16%, a gap within chance on 93 and 163 "
                 "logs). Source: px4-gps-lag, studies/mismatch-2026/GRADE.md.")
# PX4 device ids: bus type in the low 3 bits (5 = serial), device type in bits 16-23. The PX4 GPS
# driver's u-blox types: 0xA5 (model not detected), 0xA6, 0xA7, 0xA8 (M8), 0xA9 (M9), 0xAA (F9P),
# 0xAC (M10). GPS_UBX_DYNMODEL only reaches receivers that PX4's own driver configures.
UBLOX_DEVTYPES = frozenset((0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xAC))
SERIAL_BUS = 5
# px4-gps-lag studies/dynmodel-2026/GRADE.md (pre-registered, all checks held): 244 public u-blox logs,
# one per vehicle; model-6 logs a median +45.6 ms (33.2 to 55.0) later than model-7 logs of the same
# receiver class; 41.5% of model-6 logs more than 50 ms past their delay setting (model 7: 8.8%).
# GRADE-paired.md (pre-registered, D1): the two logs on PX4 PR #24831, one vehicle (u-blox F9P),
# model 6 214.2 ms vs model 7 110.5 ms, +103.7 ms.
# GRADE-mechanism.md (pre-registered, M1 = smoothing): first-order low-pass then delay, fitted on the
# same two logs: time constant 110 ms (90 to 140) at model 6 vs 20 ms (0 to 60) at model 7, +90 ms
# (59.8 to 130); delay after the filter 110 vs 95 ms, +15 ms (-20 to +50).
# GRADE-tau-fleet.md (pre-registered, H1): the same fit on dynmodel-2026's own logs, 104 at model 6 and
# 59 at model 7 in reference classes: time constant a median +110 ms (50 to 145) against the class's
# model-7 median; delay after the filter -45 ms (-75 to 0); sum +53.8 ms (40 to 80).
DYNMODEL_NOTE = ("**u-blox dynamic model 6.** This receiver runs GPS_UBX_DYNMODEL = 6 (airborne <1g), which "
                 "PX4 set for every multicopter up to v1.16. On public PX4 logs, model-6 u-blox logs lagged "
                 "a median 46 ms more than model-7 logs from the same receiver class (244 vehicles), and "
                 "about 4 in 10 were more than 50 ms past their delay setting. On the one vehicle flown with "
                 "both (the logs on PX4 PR #24831), the model-6 flight lagged 104 ms more than the model-7 "
                 "flight, and the extra lag behaved like heavier smoothing (a fitted filter time constant of about "
                 "110 ms against 20 ms), which no single GPS delay setting fully cancels. Across public logs too, "
                 "model 6's extra lag behaved like filtering: within receiver classes, the fitted time constant "
                 "was a median 110 ms longer than at model 7 (163 logs). If GPS lag matters "
                 "on this vehicle, try GPS_UBX_DYNMODEL = 7 (<2g, PX4's default "
                 "for multicopters since v1.17; it takes effect after a reboot) and measure again. Source: "
                 "px4-gps-lag, studies/dynmodel-2026/GRADE.md, GRADE-paired.md, GRADE-mechanism.md and "
                 "GRADE-tau-fleet.md.")
BATTERY = {0: "none", 1: "low", 2: "critical", 3: "emergency", 4: "failed"}
FLEET = "208 public PX4 flights (fleet-health-2026)"


def run_health(path):
    return health.measure_health(health.read_health(path))


def run_gps(path):
    """gps_lag.py's measurement and innov.py's cross-check of one log, or a
    reason why they were not run."""
    try:
        import gps_lag as gl
        import innov
    except ImportError:
        return {"lag": None, "innov": None, "skipped": "gps_lag.py and innov.py not found (run fetch_deps.py)"}
    try:
        log = gl.read_log(path)
        lag = gl.measure(log)
    except Exception as exc:  # a log without GPS or IMU topics
        return {"lag": {"passed": False, "reason": "error: %s" % str(exc)[:200]}, "innov": None}
    inn = None
    if lag.get("passed"):
        try:
            inn = innov.measure_innov(log, innov.read_innov(path))
        except Exception as exc:
            inn = {"passed": False, "reason": "error: %s" % str(exc)[:200]}
    return {"lag": lag, "innov": inn}


def run_compass(path):
    """compass.py's check of one log (studies/magcurrent-2026), or a 'not checked' result."""
    try:
        import compass
    except ImportError:
        return {"level": "not checked", "text": "Compass not checked: compass.py not found next to checkup.py."}
    try:
        return compass.check(path)
    except Exception as exc:  # a log without the topics, or unreadable
        return {"compass": getattr(compass, "__version__", None), "level": "not checked",
                "text": "Compass not checked: %s: %s." % (type(exc).__name__, str(exc)[:200])}


def run_battery(path):
    """battery_r.py's check of one log (studies/battery-r-2026), or a 'not checked' result."""
    try:
        import battery_r
    except ImportError:
        return {"level": "not checked",
                "text": "Battery resistance estimate not checked: battery_r.py not found next to checkup.py."}
    try:
        return battery_r.check(path)
    except Exception as exc:  # a log without the topics, or unreadable
        return {"battery_r": getattr(battery_r, "__version__", None), "level": "not checked",
                "text": "Battery resistance estimate not checked: %s: %s." % (type(exc).__name__, str(exc)[:200])}


def read_delay_inputs(path):
    """What applied_delay needs from one ULog: the initial parameters and, of
    the first GNSS topic present, the driver's time stamps, the corrected
    sample times and the receiver's device ids (as plain ints)."""
    from pyulog import ULog
    ulog = ULog(path, list(DELAY_TOPICS), True)
    data = {d.name: d.data for d in ulog.data_list if d.multi_id == 0}
    out = {"params": dict(ulog.initial_parameters), "topic": None, "stamps": [], "samples": [],
           "device_ids": []}
    for name in DELAY_TOPICS:
        d = data.get(name)
        if d is None:
            continue
        pre = "receiver." if name == "vehicle_gnss" else ""
        stamps = d.get(pre + "timestamp", d["timestamp"])
        samples = d.get("timestamp_sample")
        ids = d.get(pre + "device_id")
        out.update(topic=name, stamps=[int(x) for x in stamps],
                   samples=[] if samples is None else [int(x) for x in samples],
                   device_ids=[] if ids is None else [int(x) for x in ids])
        break
    return out


def _read_delay_inputs_or_none(path):
    try:
        return read_delay_inputs(path)
    except Exception:  # unreadable here; gps_lag.py reports its own reason
        return None


def _u32(x):
    return int(x) & 0xFFFFFFFF


def _i32(x):
    """A device id as PX4 stores it in an int32 parameter (SENS_GPS0_ID)."""
    x = _u32(x)
    return x - (1 << 32) if x >= (1 << 31) else x


def applied_delay(params, topic=None, stamps=(), samples=(), device_ids=()):
    """The GPS delay EKF2 applied in a log from PX4 v1.18 or newer, or None
    for older firmware (which has EKF2_GPS_DELAY; gps_lag.py reads that).

    Since PX4 PR #26660 the sensors module sets timestamp_sample =
    timestamp - SENS_GPSn_DELAY (n matched by SENS_GPSn_ID against the
    receiver's device id; by uORB instance when no id is set; 110 ms when ids
    are set and none matches), unless a PPS correction or the driver set it,
    and EKF2 fuses a sample at timestamp_sample when that is set and differs
    from timestamp, otherwise at timestamp (EKF2::UpdateGpsSample). So the
    applied delay is the median of timestamp - timestamp_sample (0 where
    they are equal). source is 'parameter' when that matches the receiver's
    slot (or the 110 ms default), else 'other' (PPS or driver timing)."""
    if "EKF2_GPS_DELAY" in params:
        return None
    prefix = next((p for p in ("SENS_GNSS", "SENS_GPS") if (p + "0_DELAY") in params), None)
    if prefix is None:
        return None
    out = {"prefix": prefix, "topic": topic, "applied_ms": None, "param": None, "param_ms": None,
           "source": None, "device_id": None, "unmatched_id": False}
    pairs = list(zip(stamps, samples))
    if not pairs:
        return out
    diffs = [(t - s) / 1000.0 if (s and s != t) else 0.0 for t, s in pairs]
    applied = round(statistics.median(diffs), 1)
    ids = [_u32(i) for i in device_ids if i]
    dev = max(sorted(set(ids)), key=ids.count) if ids else None
    slot_ids = {n: _u32(params.get("%s%d_ID" % (prefix, n)) or 0) for n in (0, 1)}
    delays = {n: params.get("%s%d_DELAY" % (prefix, n)) for n in (0, 1)}
    if any(slot_ids.values()) and dev is not None:
        match = [n for n in (0, 1) if slot_ids[n] == dev]
        slot = match[0] if match else None
        cfg = delays[slot] if match else DEFAULT_DELAY_MS
        out["unmatched_id"] = not match
    else:
        slot = next((n for n in (0, 1) if delays[n] is not None
                     and abs(applied - float(delays[n])) <= DELAY_TOL_MS), 0)
        cfg = delays[slot]
    out.update(applied_ms=applied, device_id=dev,
               param=None if slot is None else "%s%d_DELAY" % (prefix, slot),
               param_ms=None if cfg is None else float(cfg),
               source="parameter" if cfg is not None and abs(applied - float(cfg)) <= DELAY_TOL_MS else "other")
    return out


def dynmodel_info(inputs):
    """The u-blox dynamic model of one log, from read_delay_inputs' output: None unless the log has
    GPS_UBX_DYNMODEL and its GNSS receiver (the most common device id) is a u-blox on a serial port,
    which is what PX4's own GPS driver configures. note is True for model 6 (airborne <1g)."""
    if not inputs:
        return None
    value = (inputs.get("params") or {}).get("GPS_UBX_DYNMODEL")
    if value is None or isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    ids = [_u32(i) for i in inputs.get("device_ids") or () if i]
    if not ids:
        return None
    dev = max(sorted(set(ids)), key=ids.count)
    devtype = (dev >> 16) & 0xFF
    if (dev & 0x7) != SERIAL_BUS or devtype not in UBLOX_DEVTYPES:
        return None
    return {"value": value, "device_id": dev, "devtype": "0x%02X" % devtype, "note": value == 6}


def _dronecan(a):
    """True when applied_delay's receiver is on the DroneCAN (UAVCAN) bus."""
    dev = (a or {}).get("device_id")
    return dev is not None and (_u32(dev) & 0x7) == DRONECAN_BUS


def gps_verdict(lag, inn, applied=None):
    """What to tell the user about the GPS delay of one log.

    status: 'not measured', 'no setting' (no delay setting in the log), 'ok'
    (within 50 ms) or 'off'. innov: 'agrees', 'disagrees' or 'not checked'.
    suggest_ms is set only when the lag is off, EKF2's innovation does not
    lean the other way, and a parameter set the delay. near_line: ok, but
    40 ms or more off the setting. param_name: the parameter to change.
    applied (PX4 v1.18 and newer): applied_delay's reading, when the
    comparison uses it."""
    if not lag or not lag.get("passed"):
        return {"status": "not measured", "reason": (lag or {}).get("reason", "not run")}
    param, d, name = lag.get("param_gps_delay_ms"), lag.get("lag_minus_param_ms"), "EKF2_GPS_DELAY"
    used = None
    if param is None and applied and applied.get("applied_ms") is not None:
        used = applied
        param, d, name = applied["applied_ms"], lag["lag_ms"] - applied["applied_ms"], applied.get("param")
    out = {"lag_ms": lag["lag_ms"], "se_ms": lag.get("se_ms"), "param_ms": param,
           "d_ms": d, "param_name": name if param is not None else None, "innov": "not checked",
           "suggest_ms": None, "near_line": False}
    if used:
        out["applied"] = used
        out["delay_set_by"] = ("parameter" if used.get("source") == "parameter"
                               else "DroneCAN receiver" if _dronecan(used) else "PPS or GPS driver")
    if inn and inn.get("passed") and out["d_ms"]:
        x = inn["implied_excess_ms"]
        out["implied_ms"] = x
        out["innov"] = "agrees" if x * out["d_ms"] > 0 else "disagrees"
    if out["param_ms"] is None or out["d_ms"] is None:
        out["status"] = "no setting"
    elif abs(out["d_ms"]) <= OFF_MS:
        out["status"] = "ok"
        out["near_line"] = abs(out["d_ms"]) >= NEAR_MS
    else:
        out["status"] = "off"
        if out["innov"] != "disagrees":
            if used and used.get("source") != "parameter" and _dronecan(used):
                out["no_suggestion"] = DRONECAN_NOTE % used["prefix"]
            elif used and used.get("source") != "parameter":
                out["no_suggestion"] = ("the log's GPS sample times were set by PPS or the GPS driver, "
                                        "not by a delay parameter")
            else:
                out["suggest_ms"] = int(round(out["lag_ms"]))
    return out


def check(path, baseline):
    """The whole report of one log as a dict."""
    try:
        h = run_health(path)
    except Exception as exc:  # report and go on to the next log
        h = {"health_ok": False, "health_reason": "error: %s" % str(exc)[:200]}
    if baseline:
        h["fleet_place"] = health.fleet_place(h, baseline)
    g = run_gps(path)
    inputs = _read_delay_inputs_or_none(path)
    verdict = None
    if not g.get("skipped"):
        applied = applied_delay(**inputs) if inputs else None
        lag = g.get("lag") or {}
        if (inputs and inputs.get("topic") == "vehicle_gnss" and not lag.get("passed")
                and "vehicle_gps_position" in str(lag.get("reason", ""))):
            g["lag"] = dict(lag, passed=False, reason="%s (%s)" % (NEW_TOPIC_REASON, lag.get("reason", "not run")))
        verdict = gps_verdict(g.get("lag"), g.get("innov"), applied)
    return {"log": path, "checkup": __version__, "health": h, "gps": g, "gps_verdict": verdict,
            "gps_dynmodel": dynmodel_info(inputs), "compass": run_compass(path), "battery": run_battery(path)}


def _num(x, spec="%.1f"):
    return "n/a" if x is None else spec % x


def _signed(x, spec="%+.0f"):
    return spec % x


def _compare(v, near):
    """The sentence that ends the lag line: what the lag is compared with."""
    a = v.get("applied")
    diff = _signed(v["d_ms"], "%+.1f" if near else "%+.0f")
    if not a:
        return "; this vehicle's EKF2_GPS_DELAY is %.0f ms (%s ms)." % (v["param_ms"], diff)
    if a.get("source") != "parameter" and _dronecan(a):
        return ("; EKF2 fused this log's GPS samples %.0f ms before they arrived, a shift the DroneCAN "
                "receiver's own time stamps set, not %sn_DELAY (%s ms)." % (v["param_ms"], a["prefix"], diff))
    if a.get("source") != "parameter":
        return ("; EKF2 fused this log's GPS samples %.0f ms before they arrived, a shift set by PPS or the "
                "GPS driver rather than %sn_DELAY (%s ms)." % (v["param_ms"], a["prefix"], diff))
    if a.get("unmatched_id"):
        return ("; this receiver (device id %d) matches no %sn_ID, so PX4 applied its default %.0f ms (%s ms)."
                % (a["device_id"], a["prefix"], v["param_ms"], diff))
    return ("; this vehicle's %s is %.0f ms, and EKF2 fused the GPS samples that much before they arrived "
            "(%s ms)." % (v["param_name"], v["param_ms"], diff))


def render(r):
    """Markdown for one report."""
    h, v = r["health"], r.get("gps_verdict")
    place = h.get("fleet_place") or {}
    lines = ["## %s" % os.path.basename(r["log"]), ""]
    if not h.get("health_ok"):
        lines += ["Health not read: %s." % h.get("health_reason"), ""]
    else:
        kind = {True: "multicopter", False: "not a multicopter"}.get(h.get("multicopter"), "vehicle type unknown")
        lines += ["PX4 %s, %.1f min in the air, %s." % (h.get("release") or "release unknown",
                                                          h["flight_s"] / 60.0, kind), ""]
        flags = h.get("flags") or []
        if flags:
            lines += ["**%d flag%s**" % (len(flags), "" if len(flags) == 1 else "s"), ""]
            lines += ["- %s" % f for f in flags] + [""]
        else:
            lines += ["**No flags.**", ""]
        if h.get("notes"):
            lines += ["Notes:", ""] + ["- %s" % n for n in h["notes"]] + [""]
        lines += ["| Figure | This flight | Among %s |" % FLEET, "|---|---|---|"]
        shown = []

        def row(name, value, key=None):
            if key:
                shown.append(place.get(key, ""))
            lines.append("| %s | %s | %s |" % (name, value, place.get(key, "") if key else ""))

        if h.get("vib_median") is not None:
            if h.get("vib_units") == "m/s":
                row("Vibration, median", "%.3f m/s (PX4 v1.12 or older units; not banded)" % h["vib_median"])
            else:
                row("Vibration, median", "%.1f m/s² (%s)" % (h["vib_median"], h.get("vib_band")), "vib_median")
                row("Vibration, 95th percentile", "%.1f m/s²" % h["vib_p95"], "vib_p95")
        if h.get("clip_count") is not None:
            row("Accelerometer clipping", "%d events (%.2f per min)" % (h["clip_count"], h["clip_per_min"]),
                "clip_per_min")
        if h.get("jam_median") is not None:
            row("GPS jamming indicator, median", "%.0f (docs: about 40 or below)" % h["jam_median"], "jam_median")
        if h.get("jam_state_max") is not None:
            row("GPS receiver jamming state, max", "%d (%s)" % (h["jam_state_max"],
                                                              health.STATE_NAMES.get(h["jam_state_max"], "?")))
        if h.get("sats_median") is not None:
            row("Satellites used, median", "%.0f" % h["sats_median"], "sats_median")
        if h.get("eph_median") is not None:
            row("eph, median", "%.2f m" % h["eph_median"], "eph_median")
        if h.get("battery_warning_max") is not None:
            row("Battery warning, worst", BATTERY.get(h["battery_warning_max"], str(h["battery_warning_max"])))
        if h.get("failsafe_in_flight") is not None:
            row("Failsafe in flight", "yes" if h["failsafe_in_flight"] else "no")
        if h.get("sat_share") is not None:
            row("A motor at full command", "%.1f%% of flight" % (100 * h["sat_share"]), "sat_share")
        lines.append("")
        if any("(tied)" in p for p in shown):
            lines += ["*(tied)*: many public flights have exactly this value (for clipping and motor "
                      "saturation, usually zero), so this flight sits with them.", ""]
    lines += ["### GPS delay", ""]
    g = r.get("gps") or {}
    if g.get("skipped"):
        lines += ["Not checked: %s." % g["skipped"], ""]
    elif v["status"] == "not measured":
        lines += ["Not measured: %s. The lag needs a flight with some manoeuvring; hover-only flights "
                  "usually can't be measured." % v["reason"], ""]
    else:
        near = v.get("near_line")
        text = "The GPS lags the IMU by **%s ms** (± %s)" % (_num(v["lag_ms"], "%.1f" if near else "%.0f"),
                                                            _num(v["se_ms"]))
        if v["status"] == "no setting":
            text += (". The log has no GPS delay setting to compare with (EKF2_GPS_DELAY, or SENS_GPS0_DELAY "
                     "from PX4 v1.18 on).")
        else:
            text += _compare(v, near)
        lines += [text, ""]
        if v.get("applied"):
            lines += ["(PX4 v1.18 replaced EKF2_GPS_DELAY with a delay per receiver, SENS_GPS0_DELAY and "
                      "SENS_GPS1_DELAY; PX4 main names them SENS_GNSS0_DELAY and SENS_GNSS1_DELAY. The delay "
                      "above is read from the log's own sample times.)", ""]
        if v["innov"] == "agrees":
            lines += ["EKF2's own GPS velocity innovations lean the same way (implied %s ms)." % _signed(v["implied_ms"]), ""]
        elif v["innov"] == "disagrees":
            lines += ["EKF2's own GPS velocity innovations lean the other way (implied %s ms), so the two "
                      "methods disagree on this flight." % _signed(v["implied_ms"]), ""]
        else:
            reason = ((g.get("innov") or {}).get("reason")) or "not run"
            lines += ["EKF2's innovations could not be checked (%s)." % reason, ""]
        if v["status"] == "ok" and near:
            lines += ["Within %.0f ms of the setting, but close to the %.0f ms line (%s ms): no change "
                      "suggested; measure another flight to see which side it falls on."
                      % (OFF_MS, OFF_MS, _signed(v["d_ms"], "%+.1f")), ""]
        elif v["status"] == "ok":
            lines += ["Within %.0f ms of the setting: nothing to change." % OFF_MS, ""]
        elif v["status"] == "off" and v["suggest_ms"] is not None:
            a = v.get("applied") or {}
            if a.get("unmatched_id"):
                what = "%s0_ID = %d and %s0_DELAY = %d" % (a["prefix"], _i32(a["device_id"]), a["prefix"],
                                                           v["suggest_ms"])
            else:
                what = "%s = %d" % (v.get("param_name") or "EKF2_GPS_DELAY", v["suggest_ms"])
            lines += ["**Suggestion:** try %s as a starting value. Measure two or three "
                      "flights first (one vehicle's flights agree within a few ms), and check the change with "
                      "EKF2 log replay before flying it. On public logs, about 30%% of vehicles are more than "
                      "50 ms off their setting." % what, ""]
        elif v["status"] == "off" and v.get("no_suggestion"):
            lines += ["More than %.0f ms off, but %s, so there is no parameter to suggest; look at this "
                      "flight in EKF2 log replay." % (OFF_MS, v["no_suggestion"]), ""]
        elif v["status"] == "off":
            lines += ["More than %.0f ms off, but with the innovations disagreeing there is no suggestion; "
                      "look at this flight in EKF2 log replay." % OFF_MS, ""]
        if v["status"] == "off":
            lines += [MISMATCH_NOTE, ""]
    if (r.get("gps_dynmodel") or {}).get("note"):
        lines += [DYNMODEL_NOTE, ""]
    c = r.get("compass")
    if c:
        text = c.get("text") or "Compass not checked."
        lines += ["### Compass", "", ("**Flag:** " + text) if c.get("level") == "flag" else text, ""]
    b = r.get("battery")
    if b:
        text = b.get("text") or "Battery resistance estimate not checked."
        lines += ["### Battery", "", ("**Note:** " + text) if b.get("level") == "note" else text, ""]
    return "\n".join(lines)


def _plain(x):
    """numpy scalars and arrays as plain JSON values."""
    return x.tolist() if hasattr(x, "tolist") else str(x)


def flagged(r):
    return bool((r["health"].get("flags") or []) or (r.get("gps_verdict") or {}).get("suggest_ms") is not None
                or (r.get("compass") or {}).get("level") == "flag")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("logs", nargs="+")
    p.add_argument("--md", help="append the Markdown report here (default: stdout)")
    p.add_argument("--json", help="write one JSON line per log here")
    p.add_argument("--baseline", default=health.BASELINE)
    p.add_argument("--fail-on-flag", action="store_true")
    args = p.parse_args(argv)
    baseline = health.load_baseline(args.baseline)
    reports = [check(path, baseline) for path in args.logs]
    head = ("# PX4 log health\n\n%d log%s, checked with px4-log-health checkup %s by MV2 of Munim, Inc. "
            "(made with AI agents). A flag is a reason to look at the log in Flight Review, not a diagnosis.\n"
            % (len(reports), "" if len(reports) == 1 else "s", __version__))
    md = "\n".join([head] + [render(r) for r in reports]) + "\n"
    if args.md:
        with open(args.md, "a", encoding="utf-8") as f:
            f.write(md)
    else:
        sys.stdout.write(md)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            for r in reports:
                f.write(json.dumps(r, default=_plain) + "\n")
    return 1 if args.fail_on_flag and any(flagged(r) for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""checkup.py: one readable report per PX4 flight log.

Runs health.py on each log and places every figure against 208 public
flights (baseline.json). When px4-gps-lag's gps_lag.py and innov.py sit next
to this file (fetch_deps.py or the GitHub Action puts them there), it also
measures how far the GPS lags the IMU, compares that with the log's
EKF2_GPS_DELAY, and checks the direction against EKF2's own GPS velocity
innovations (px4-gps-lag, studies/fleet-2026/GRADE-innov-confirm.md).

    python checkup.py flight.ulg [more.ulg ...] [--md report.md] [--json rows.jsonl] [--fail-on-flag]

Markdown goes to --md (appended, so it can be $GITHUB_STEP_SUMMARY) or to
stdout; one JSON line per log goes to --json. With --fail-on-flag the exit
code is 1 if any log raised a flag. Reads no position, so no location
leaves the log. A flag is a reason to look at the log in Flight Review, and
a suggested EKF2_GPS_DELAY is a starting value to check with EKF2 log
replay, not a setting to fly.

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import health

__version__ = "0.3.1"

OFF_MS = 50.0           # px4-gps-lag rule D1: suggest only when the lag is this far off EKF2_GPS_DELAY
NEAR_MS = 40.0          # within OFF_MS but this close to it: say so, with one decimal
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


def gps_verdict(lag, inn):
    """What to tell the user about the GPS delay of one log.

    status: 'not measured', 'no setting' (EKF2_GPS_DELAY not in the log), 'ok'
    (within 50 ms) or 'off'. innov: 'agrees', 'disagrees' or 'not checked'.
    suggest_ms is set only when the lag is off and EKF2's innovation does not
    lean the other way. near_line: ok, but 40 ms or more off the setting."""
    if not lag or not lag.get("passed"):
        return {"status": "not measured", "reason": (lag or {}).get("reason", "not run")}
    out = {"lag_ms": lag["lag_ms"], "se_ms": lag.get("se_ms"), "param_ms": lag.get("param_gps_delay_ms"),
           "d_ms": lag.get("lag_minus_param_ms"), "innov": "not checked", "suggest_ms": None,
           "near_line": False}
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
    return {"log": path, "checkup": __version__, "health": h, "gps": g,
            "gps_verdict": gps_verdict(g.get("lag"), g.get("innov")) if not g.get("skipped") else None}


def _num(x, spec="%.1f"):
    return "n/a" if x is None else spec % x


def _signed(x, spec="%+.0f"):
    return spec % x


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
            text += ". The log has no EKF2_GPS_DELAY to compare with."
        else:
            text += "; this vehicle's EKF2_GPS_DELAY is %.0f ms (%s ms)." % (
                v["param_ms"], _signed(v["d_ms"], "%+.1f" if near else "%+.0f"))
        lines += [text, ""]
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
            lines += ["**Suggestion:** try EKF2_GPS_DELAY = %d as a starting value. Measure two or three "
                      "flights first (one vehicle's flights agree within a few ms), and check the change with "
                      "EKF2 log replay before flying it. On public logs, about 30%% of vehicles are more than "
                      "50 ms off their setting." % v["suggest_ms"], ""]
        elif v["status"] == "off":
            lines += ["More than %.0f ms off, but with the innovations disagreeing there is no suggestion; "
                      "look at this flight in EKF2 log replay." % OFF_MS, ""]
    return "\n".join(lines)


def _plain(x):
    """numpy scalars and arrays as plain JSON values."""
    return x.tolist() if hasattr(x, "tolist") else str(x)


def flagged(r):
    return bool((r["health"].get("flags") or []) or (r.get("gps_verdict") or {}).get("suggest_ms") is not None)


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

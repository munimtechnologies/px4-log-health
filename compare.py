"""compare.py: what changed between two PX4 flight logs of one vehicle.

Give it a log from before a change (firmware upgrade, receiver swap, new
parameters, new props) and one from after. It runs checkup.py's checks on
both and lists, item by item, whether each figure stayed within the rule,
got better, got worse, or could not be compared, and which parameters
differ between the two logs.

The rules (C1-C8 below) were fixed in px4-log-health's
studies/compare-2026/PLAN.md before any log was read. That study measures
how often each rule fires on public pairs of flights of one vehicle where
nothing was changed, which is the false-alarm rate of this comparison.

    python compare.py before.ulg after.ulg [--md report.md] [--json result.json]
                      [--fail-on-worse] [--min-compared N]

A 'worse' item is a reason to look at both logs in Flight Review, not a
verdict on the change. Two flights also differ in wind, payload, battery and
how they were flown, and 'environment' items (satellites, eph) are listed
but never counted against the vehicle. Passing every item does not show that
the vehicle is safe to fly.

Changes in 0.2.0 (suggested by yigitcan-ozturk on discuss.px4.io topic 49705,
post 13; the thresholds are unchanged, so the frozen 0.1.0 study still applies):
- C1/C2: a missing standard error (or one that is 0 or not finite) is no
  longer taken as 0. A change within 30 ms is still 'same' (the rule needs
  both conditions to fire); a larger change with no standard error on one
  log is 'not comparable', because it cannot be weighed against its noise.
- The verdict and the report's headline say how many of the 10 vehicle
  checks could be compared and name the ones that could not, so a report
  with missing data does not read as full assurance.
- The report states that the same logged firmware and settings do not mean
  the same physical vehicle.
- --min-compared N exits 2 when fewer than N vehicle checks were compared.

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys

__version__ = "0.2.0"

LAG_MS = 30.0          # C1/C2: a GPS lag or innovation change must exceed this many ms ...
LAG_SE = 2.0           # ... and this many combined standard errors
VIB_RATIO = 1.5        # C3: vibration median up by half (or down by a third) ...
VIB_ABS = 0.5          # ... and by at least this many m/s^2
CLIP_RATIO = 3.0       # C4: clipping rate, when both flights clipped
SAT_POINTS = 0.05      # C5: share of flight with a motor at full command, 5 percentage points
SATS_DROP = 3.0        # E1: satellites used, median (environment)
EPH_RATIO = 1.5        # E2: eph, median (environment)
LENGTH_RATIO = 3.0     # a note when one flight is this many times longer than the other
COMPASS_ORDER = {"weak": 0, "moderate": 1, "strong": 2}
GPS_STATUSES = ("ok", "off")
# Parameters PX4 changes by itself from flight to flight: counters, and values it learns and saves.
VOLATILE = frozenset(("LND_FLIGHT_T_HI", "LND_FLIGHT_T_LO", "COM_FLIGHT_UUID"))
LEARNED = re.compile(r"^(CAL_(ACC|GYRO|MAG|BARO)\d_([XYZ])?OFF|CAL_(ACC|GYRO|MAG|BARO)\d_TEMP|"
                     r"EKF2_MAG_DECL|SENS_DPRES_OFF)$")
VEHICLE, ENVIRONMENT, CONFIG = "vehicle", "environment", "config"
PHYSICAL_CAVEAT = ("The same firmware and settings in both logs do not mean the same physical vehicle: payload, "
                   "battery condition, props and how it was flown can differ between the two flights.")


def _num(x):
    """x as a finite float, or None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _se(x):
    """A standard error as a positive finite float, or None (missing, 0, negative or not finite)."""
    x = _num(x)
    return x if x is not None and x > 0 else None


def _item(name, kind, status, before=None, after=None, delta=None, rule="", why=""):
    return {"item": name, "kind": kind, "status": status, "before": before, "after": after,
            "delta": delta, "rule": rule, "why": why}


def _na(name, kind, why, before=None, after=None, rule="", delta=None):
    return _item(name, kind, "not comparable", before, after, delta, rule, why)


def _which(missing_b, missing_a):
    return "both logs" if missing_b and missing_a else ("the before log" if missing_b else "the after log")


def param_class(name):
    """'volatile' (a counter PX4 updates itself), 'learned' (a value PX4 learns and saves) or 'setting'."""
    if name in VOLATILE:
        return "volatile"
    if LEARNED.match(name):
        return "learned"
    return "setting"


def same_value(a, b):
    """Parameter values equal (floats are float32 in a ULog, so with a small tolerance)."""
    if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) and not isinstance(b, bool):
        return a == b
    fa, fb = _num(a), _num(b)
    if fa is None or fb is None:
        return a == b
    return math.isclose(fa, fb, rel_tol=1e-6, abs_tol=1e-9)


def param_diff(before, after):
    """{'settings': [...], 'learned': [...], 'volatile': [...]}: each a sorted list of
    {'name', 'before', 'after'} for parameters that differ or exist in only one log (None there)."""
    out = {"settings": [], "learned": [], "volatile": []}
    if before is None or after is None:
        return None
    for name in sorted(set(before) | set(after)):
        a, b = before.get(name), after.get(name)
        if name in before and name in after and same_value(a, b):
            continue
        cls = param_class(name)
        key = {"setting": "settings", "learned": "learned", "volatile": "volatile"}[cls]
        out[key].append({"name": name, "before": a, "after": b})
    return out


def _lag_change(name, rule, xb, xa, d, seb, sea):
    """The shared C1/C2 decision. Returns (status or None, why). None means 'goes on to direction'."""
    if abs(d) <= LAG_MS:
        why = "within %.0f ms" % LAG_MS
        if seb is None or sea is None:
            why += " (no standard error on %s)" % _which(seb is None, sea is None)
        return "same", why
    if seb is None or sea is None:
        return "not comparable", ("changed by more than %.0f ms, but there is no standard error on %s, so the "
                                  "change cannot be weighed against its noise" % (LAG_MS, _which(seb is None, sea is None)))
    se = math.sqrt(seb ** 2 + sea ** 2)
    if abs(d) <= LAG_SE * se:
        return "same", "within %.0f combined SE (%.1f ms)" % (LAG_SE, se)
    return None, "combined SE %.1f ms" % se


def _gps_lag(rb, ra):
    """C1: the GPS lag behind the IMU (gps_lag.py, via checkup's gps_verdict)."""
    rule = "C1: |change| > %.0f ms and > %.0f combined SE" % (LAG_MS, LAG_SE)
    vb, va = rb.get("gps_verdict") or {}, ra.get("gps_verdict") or {}
    lb, la = _num(vb.get("lag_ms")), _num(va.get("lag_ms"))
    if lb is None or la is None:
        return _na("gps_lag_ms", VEHICLE, "GPS lag not measured on %s" % _which(lb is None, la is None),
                   lb, la, rule)
    d = round(la - lb, 1)
    status, why = _lag_change("gps_lag_ms", rule, lb, la, d, _se(vb.get("se_ms")), _se(va.get("se_ms")))
    if status == "not comparable":
        return _na("gps_lag_ms", VEHICLE, why, lb, la, rule, d)
    if status:
        return _item("gps_lag_ms", VEHICLE, status, lb, la, d, rule, why)
    db, da = _num(vb.get("d_ms")), _num(va.get("d_ms"))
    if db is None or da is None:
        return _item("gps_lag_ms", VEHICLE, "changed", lb, la, d, rule,
                     "the lag changed; no delay setting on both logs to say whether that is better")
    if abs(da) < abs(db):
        return _item("gps_lag_ms", VEHICLE, "better", lb, la, d, rule,
                     "the lag moved closer to the delay EKF2 assumed (%+.0f -> %+.0f ms off)" % (db, da))
    return _item("gps_lag_ms", VEHICLE, "worse", lb, la, d, rule,
                 "the lag moved further from the delay EKF2 assumed (%+.0f -> %+.0f ms off)" % (db, da))


def _gps_match(rb, ra):
    """C1b: the lag within 50 ms of the delay EKF2 assumed ('ok') or not ('off')."""
    rule = "C1b: checkup's GPS delay status ok/off"
    sb = (rb.get("gps_verdict") or {}).get("status")
    sa = (ra.get("gps_verdict") or {}).get("status")
    if sb not in GPS_STATUSES or sa not in GPS_STATUSES:
        return _na("gps_delay_status", VEHICLE, "status %s -> %s" % (sb, sa), sb, sa, rule)
    if sb == sa:
        return _item("gps_delay_status", VEHICLE, "same", sb, sa, None, rule)
    return _item("gps_delay_status", VEHICLE, "worse" if sa == "off" else "better", sb, sa, None, rule,
                 "now more than 50 ms off the delay setting" if sa == "off" else "now within 50 ms of the setting")


def _innov(rb, ra):
    """C2: the delay excess implied by EKF2's own GPS velocity innovations (innov.py)."""
    rule = "C2: |implied excess| changes by > %.0f ms and > %.0f combined SE" % (LAG_MS, LAG_SE)
    ib = (rb.get("gps") or {}).get("innov") or {}
    ia = (ra.get("gps") or {}).get("innov") or {}
    xb = _num(ib.get("implied_excess_ms")) if ib.get("passed") else None
    xa = _num(ia.get("implied_excess_ms")) if ia.get("passed") else None
    if xb is None or xa is None:
        return _na("gps_innov_excess_ms", VEHICLE, "EKF2 innovations not checked on %s" % _which(xb is None, xa is None),
                   xb, xa, rule)
    d = round(abs(xa) - abs(xb), 1)
    status, why = _lag_change("gps_innov_excess_ms", rule, xb, xa, d,
                              _se(ib.get("implied_se_ms")), _se(ia.get("implied_se_ms")))
    if status == "not comparable":
        return _na("gps_innov_excess_ms", VEHICLE, why, xb, xa, rule, d)
    if status:
        return _item("gps_innov_excess_ms", VEHICLE, status, xb, xa, d, rule, why)
    return _item("gps_innov_excess_ms", VEHICLE, "better" if d < 0 else "worse", xb, xa, d, rule,
                 "EKF2's innovations imply %s mismatch with the delay setting" % ("less" if d < 0 else "more"))


def _health(r):
    h = r.get("health") or {}
    return h if h.get("health_ok") else None


def _vibration(rb, ra):
    """C3: median in-flight vibration (vehicle_imu_status, IMU 0)."""
    rule = "C3: ratio >= %.1f (or <= 1/%.1f) and |change| >= %.1f m/s^2" % (VIB_RATIO, VIB_RATIO, VIB_ABS)
    hb, ha = _health(rb), _health(ra)
    if hb is None or ha is None:
        return _na("vibration_median", VEHICLE, "health not read on %s" % _which(hb is None, ha is None), rule=rule)
    vb, va = _num(hb.get("vib_median")), _num(ha.get("vib_median"))
    if vb is None or va is None:
        return _na("vibration_median", VEHICLE, "vibration not logged on %s" % _which(vb is None, va is None),
                   vb, va, rule)
    if hb.get("vib_units") == "m/s" or ha.get("vib_units") == "m/s":
        return _na("vibration_median", VEHICLE, "PX4 v1.12 or older units (m/s) on one log", vb, va, rule)
    d = round(va - vb, 3)
    ratio = va / vb if vb > 0 else (math.inf if va > 0 else 1.0)
    bands = "%s -> %s" % (hb.get("vib_band"), ha.get("vib_band"))
    if ratio >= VIB_RATIO and d >= VIB_ABS:
        return _item("vibration_median", VEHICLE, "worse", vb, va, d, rule, "up %.0f%% (%s)" % (100 * (ratio - 1), bands))
    if ratio <= 1.0 / VIB_RATIO and d <= -VIB_ABS:
        return _item("vibration_median", VEHICLE, "better", vb, va, d, rule, "down %.0f%% (%s)" % (100 * (1 - ratio), bands))
    return _item("vibration_median", VEHICLE, "same", vb, va, d, rule, bands)


def _clipping(rb, ra):
    """C4: accelerometer clipping events per minute in flight."""
    rule = "C4: clipping starts or stops, or its rate changes %.0f-fold" % CLIP_RATIO
    hb, ha = _health(rb), _health(ra)
    if hb is None or ha is None:
        return _na("clipping_per_min", VEHICLE, "health not read on %s" % _which(hb is None, ha is None), rule=rule)
    cb, ca = hb.get("clip_count"), ha.get("clip_count")
    pb, pa = _num(hb.get("clip_per_min")), _num(ha.get("clip_per_min"))
    if cb is None or ca is None or pb is None or pa is None:
        return _na("clipping_per_min", VEHICLE, "clipping not logged on one log", pb, pa, rule)
    if cb == 0 and ca == 0:
        return _item("clipping_per_min", VEHICLE, "same", pb, pa, 0.0, rule, "no clipping on either flight")
    if cb == 0:
        return _item("clipping_per_min", VEHICLE, "worse", pb, pa, pa, rule, "clipping appeared (%d events)" % ca)
    if ca == 0:
        return _item("clipping_per_min", VEHICLE, "better", pb, pa, -pb, rule, "clipping stopped")
    d = round(pa - pb, 3)
    if pa >= CLIP_RATIO * pb:
        return _item("clipping_per_min", VEHICLE, "worse", pb, pa, d, rule)
    if pa * CLIP_RATIO <= pb:
        return _item("clipping_per_min", VEHICLE, "better", pb, pa, d, rule)
    return _item("clipping_per_min", VEHICLE, "same", pb, pa, d, rule)


def _saturation(rb, ra):
    """C5: share of in-flight samples with a motor at full command."""
    rule = "C5: |change| >= %.0f percentage points" % (100 * SAT_POINTS)
    hb, ha = _health(rb), _health(ra)
    if hb is None or ha is None:
        return _na("motor_saturation_share", VEHICLE, "health not read on %s" % _which(hb is None, ha is None),
                   rule=rule)
    sb, sa = _num(hb.get("sat_share")), _num(ha.get("sat_share"))
    if sb is None or sa is None:
        return _na("motor_saturation_share", VEHICLE, "motor commands not read on %s" % _which(sb is None, sa is None),
                   sb, sa, rule)
    d = round(sa - sb, 4)
    if d >= SAT_POINTS - 1e-9:
        return _item("motor_saturation_share", VEHICLE, "worse", sb, sa, d, rule)
    if d <= -SAT_POINTS + 1e-9:
        return _item("motor_saturation_share", VEHICLE, "better", sb, sa, d, rule)
    return _item("motor_saturation_share", VEHICLE, "same", sb, sa, d, rule)


def _compass(rb, ra):
    """C6: how strongly the compass field norm moves with battery current (compass.py's class)."""
    rule = "C6: coupling class weak/moderate/strong changes"
    cb, ca = (rb.get("compass") or {}).get("class"), (ra.get("compass") or {}).get("class")
    pb, pa = _num((rb.get("compass") or {}).get("coupling_pct")), _num((ra.get("compass") or {}).get("coupling_pct"))
    if cb not in COMPASS_ORDER or ca not in COMPASS_ORDER:
        return _na("compass_coupling", VEHICLE, "compass coupling not measurable on %s" % _which(
            cb not in COMPASS_ORDER, ca not in COMPASS_ORDER), cb, ca, rule)
    d = None if pb is None or pa is None else round(pa - pb, 3)
    why = "%s -> %s" % ("n/a" if pb is None else "%.1f%%" % pb, "n/a" if pa is None else "%.1f%%" % pa)
    if COMPASS_ORDER[ca] > COMPASS_ORDER[cb]:
        return _item("compass_coupling", VEHICLE, "worse", cb, ca, d, rule, why)
    if COMPASS_ORDER[ca] < COMPASS_ORDER[cb]:
        return _item("compass_coupling", VEHICLE, "better", cb, ca, d, rule, why)
    return _item("compass_coupling", VEHICLE, "same", cb, ca, d, rule, why)


def _battery_r(rb, ra):
    """C7: PX4's online battery-resistance estimate collapsed to 0 (battery_r.py)."""
    rule = "C7: collapse appears or goes away"
    bb, ba = (rb.get("battery") or {}).get("collapsed"), (ra.get("battery") or {}).get("collapsed")
    if not isinstance(bb, bool) or not isinstance(ba, bool):
        return _na("battery_r_collapse", VEHICLE, "battery-resistance estimate not measurable on %s" % _which(
            not isinstance(bb, bool), not isinstance(ba, bool)), bb, ba, rule)
    if bb == ba:
        return _item("battery_r_collapse", VEHICLE, "same", bb, ba, None, rule)
    return _item("battery_r_collapse", VEHICLE, "worse" if ba else "better", bb, ba, None, rule,
                 "the estimate collapsed to 0 on the after flight" if ba else "no collapse on the after flight")


def _event(name, rule, key, test):
    def check(rb, ra):
        hb, ha = _health(rb), _health(ra)
        if hb is None or ha is None or hb.get(key) is None or ha.get(key) is None:
            return _na(name, VEHICLE, "not logged on one log", rule=rule)
        xb, xa = test(hb.get(key)), test(ha.get(key))
        if xb == xa:
            return _item(name, VEHICLE, "same", hb.get(key), ha.get(key), None, rule)
        return _item(name, VEHICLE, "worse" if xa else "better", hb.get(key), ha.get(key), None, rule)
    check.__name__ = "_" + name
    return check


_battery_warning = _event("battery_warning", "C8: an in-flight battery warning appears or goes away",
                          "battery_warning_max", lambda w: int(w) >= 1)
_failsafe = _event("failsafe_in_flight", "C8: a failsafe in flight appears or goes away",
                   "failsafe_in_flight", lambda f: bool(f))


def _sats(rb, ra):
    rule = "E1: satellites used, median, changes by %.0f or more" % SATS_DROP
    hb, ha = _health(rb), _health(ra)
    sb = _num((hb or {}).get("sats_median"))
    sa = _num((ha or {}).get("sats_median"))
    if sb is None or sa is None:
        return _na("satellites_median", ENVIRONMENT, "not logged on one log", sb, sa, rule)
    d = sa - sb
    st = "worse" if d <= -SATS_DROP else ("better" if d >= SATS_DROP else "same")
    return _item("satellites_median", ENVIRONMENT, st, sb, sa, d, rule)


def _eph(rb, ra):
    rule = "E2: eph median changes %.1f-fold" % EPH_RATIO
    hb, ha = _health(rb), _health(ra)
    eb = _num((hb or {}).get("eph_median"))
    ea = _num((ha or {}).get("eph_median"))
    if eb is None or ea is None or eb <= 0 or ea <= 0:
        return _na("eph_median", ENVIRONMENT, "not logged on one log", eb, ea, rule)
    ratio = ea / eb
    st = "worse" if ratio >= EPH_RATIO else ("better" if ratio <= 1.0 / EPH_RATIO else "same")
    return _item("eph_median", ENVIRONMENT, st, eb, ea, round(ea - eb, 3), rule)


CHECKS = (_gps_lag, _gps_match, _innov, _vibration, _clipping, _saturation, _compass, _battery_r,
          _battery_warning, _failsafe, _sats, _eph)
VEHICLE_CHECKS = 10


def coverage_of(items):
    """{'compared', 'checks', 'not_comparable': [names], 'complete'} over the vehicle items."""
    veh = [i for i in items if i["kind"] == VEHICLE]
    missing = [i["item"] for i in veh if i["status"] == "not comparable"]
    return {"compared": len(veh) - len(missing), "checks": len(veh), "not_comparable": missing,
            "complete": not missing}


def compare_rows(rb, ra, params_b=None, params_a=None):
    """Compare two checkup.check() rows (before, after) and, when given, their initial parameters.

    Returns {'compare', 'items', 'firmware', 'params', 'notes', 'worse', 'better', 'compared',
    'coverage', 'verdict'}."""
    items = []
    for check in CHECKS:
        try:
            items.append(check(rb, ra))
        except Exception as exc:  # one item failing must not hide the others
            items.append(_na(check.__name__.strip("_"), VEHICLE, "error: %s" % str(exc)[:200]))
    fb = ((rb.get("health") or {}).get("release"))
    fa = ((ra.get("health") or {}).get("release"))
    firmware = {"before": fb, "after": fa, "changed": bool(fb and fa and fb != fa)}
    pd = param_diff(params_b, params_a)
    notes = []
    tb = _num((rb.get("health") or {}).get("flight_s"))
    ta = _num((ra.get("health") or {}).get("flight_s"))
    if tb and ta and max(tb, ta) >= LENGTH_RATIO * min(tb, ta):
        notes.append("the two flights differ a lot in length (%.1f and %.1f min in the air)" % (tb / 60, ta / 60))
    worse = [i["item"] for i in items if i["kind"] == VEHICLE and i["status"] == "worse"]
    better = [i["item"] for i in items if i["kind"] == VEHICLE and i["status"] == "better"]
    compared = [i for i in items if i["kind"] == VEHICLE and i["status"] != "not comparable"]
    cov = coverage_of(items)
    if not compared:
        verdict = "not comparable"
    else:
        if worse:
            verdict = "worse on %d item%s" % (len(worse), "" if len(worse) == 1 else "s")
        elif better or any(i["status"] == "changed" for i in compared):
            verdict = "changed, nothing worse"
        else:
            verdict = "no change beyond the rules"
        if not cov["complete"]:
            verdict += " (only %d of %d checks comparable)" % (cov["compared"], cov["checks"])
    return {"compare": __version__, "items": items, "firmware": firmware, "params": pd, "notes": notes,
            "worse": worse, "better": better, "compared": len(compared), "coverage": cov, "verdict": verdict}


def _fmt(x):
    if x is None:
        return "n/a"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, float):
        return ("%.3f" % x).rstrip("0").rstrip(".") if abs(x) < 10 else "%.1f" % x
    return str(x)


def render(result, name_b="before", name_a="after"):
    """Markdown for one comparison."""
    lines = ["# PX4 log compare", "",
             "%s (before) against %s (after), px4-log-health compare %s by MV2 of Munim, Inc. (made with AI "
             "agents). A 'worse' item is a reason to look at both logs, not a verdict on the change; rules: "
             "studies/compare-2026/PLAN.md." % (name_b, name_a, result["compare"]), "",
             "**Verdict: %s.**" % result["verdict"], ""]
    cov = result.get("coverage") or coverage_of(result["items"])
    line = "Checks compared: %d of %d vehicle checks" % (cov["compared"], cov["checks"])
    if cov["not_comparable"]:
        line += "; not comparable (missing or unusable data, never counted as a pass): %s" % ", ".join(
            cov["not_comparable"])
    lines += [line + ".", ""]
    fw = result["firmware"]
    lines += ["Firmware: %s -> %s%s." % (fw["before"] or "unknown", fw["after"] or "unknown",
                                          " (changed)" if fw["changed"] else ""), ""]
    for n in result["notes"]:
        lines += ["Note: %s." % n, ""]
    lines += ["| Item | Before | After | Change | Result |", "|---|---|---|---|---|"]
    for i in result["items"]:
        res = i["status"] + (" (environment)" if i["kind"] == ENVIRONMENT and i["status"] in ("worse", "better") else "")
        if i["why"]:
            res += ": " + i["why"]
        lines.append("| %s | %s | %s | %s | %s |" % (i["item"], _fmt(i["before"]), _fmt(i["after"]),
                                                     _fmt(i["delta"]), res))
    lines.append("")
    pd = result["params"]
    if pd is None:
        lines += ["Parameters not compared.", ""]
    else:
        s = pd["settings"]
        lines += ["%d setting%s differ%s between the two logs%s" % (
            len(s), "" if len(s) == 1 else "s", "s" if len(s) == 1 else "", ":" if s else ".")]
        if s:
            lines.append("")
            lines += ["- %s: %s -> %s" % (p["name"], _fmt(p["before"]), _fmt(p["after"])) for p in s[:60]]
            if len(s) > 60:
                lines.append("- ... and %d more" % (len(s) - 60))
        lines += ["", "Values PX4 learns and saves itself (sensor offsets, declination): %d changed." % len(pd["learned"]), ""]
    lines += [PHYSICAL_CAVEAT + " Passing every item does not show that the vehicle is safe to fly.", ""]
    return "\n".join(lines) + "\n"


def exit_code(result, fail_on_worse=False, min_compared=None):
    """1 if --fail-on-worse and something got worse, else 2 if fewer than --min-compared checks compared, else 0."""
    if fail_on_worse and result["worse"]:
        return 1
    if min_compared is not None and result["compared"] < min_compared:
        return 2
    return 0


def read_params(path):
    """The initial parameters of one ULog."""
    from pyulog import ULog
    return dict(ULog(path, ["vehicle_status"], True).initial_parameters)


def compare_logs(before, after, baseline=None):
    """Run checkup on both logs and compare them."""
    import checkup
    import health
    if baseline is None:
        baseline = health.load_baseline(health.BASELINE)
    rb, ra = (plain_row(checkup.check(path, baseline)) for path in (before, after))
    try:
        pb, pa = read_params(before), read_params(after)
    except Exception:
        pb = pa = None
    out = compare_rows(rb, ra, pb, pa)
    out["checkup"] = checkup.__version__
    return out, rb, ra


def _plain(x):
    return x.tolist() if hasattr(x, "tolist") else str(x)


def plain_row(row):
    """A checkup row with numpy values made plain (as checkup's --json writes it)."""
    return json.loads(json.dumps(row, default=_plain))


def main(argv=None):
    import os
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--md", help="append the Markdown here (default: stdout)")
    p.add_argument("--json", help="write the comparison as JSON here")
    p.add_argument("--fail-on-worse", action="store_true", help="exit 1 if any vehicle item got worse")
    p.add_argument("--min-compared", type=int, default=None,
                   help="exit 2 if fewer than this many of the 10 vehicle checks could be compared")
    args = p.parse_args(argv)
    result, _, _ = compare_logs(args.before, args.after)
    md = render(result, os.path.basename(args.before), os.path.basename(args.after))
    if args.md:
        with open(args.md, "a", encoding="utf-8") as f:
            f.write(md)
    else:
        sys.stdout.write(md)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1, default=_plain)
    return exit_code(result, args.fail_on_worse, args.min_compared)


if __name__ == "__main__":
    raise SystemExit(main())

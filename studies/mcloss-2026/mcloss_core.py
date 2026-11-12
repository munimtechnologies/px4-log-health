"""mcloss_core.py: pure-Python core of the mcloss-2026 census (in-flight manual-control loss in PX4 logs).

Every logged topic is a step function: a sample's value holds from its timestamp until the next sample of the
same topic, and the last sample holds until the end of the log. Before a topic's first sample its value is
unknown and never satisfies a predicate. Times are seconds (float).

Definitions (draft; frozen only when studies/mcloss-2026/PLAN.md is published):
  * armed      vehicle_status.arming_state == 2 (ARMING_STATE_ARMED)
  * airborne   armed AND vehicle_land_detected.landed is False
  * loss event a false -> true transition of failsafe_flags.manual_control_signal_lost whose onset lies inside
               an airborne interval and whose true run lasts >= min_s seconds (default 0.5 s, the threshold the
               flight-review-rs rc_loss analyzer uses). A run that is already true at the topic's first sample
               is not an event: the link was never seen, which is a different finding (see link_status).
  * source     last manual_control_setpoint.data_source at or before the onset, at most max_age old:
               1 = rc, 2..7 = mavlink (instances 0..5), 0 = unknown, nothing recent = none
  * rc_agrees  input_rc.rc_lost or input_rc.rc_failsafe is true at some point in [onset - 1 s, end + 1 s]
  * nav_after  vehicle_status.nav_state holding 2 s after the onset

No numpy, no pyulog: the census driver extracts arrays and calls these functions.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

from __future__ import annotations

__version__ = "0.1.0"

ARMED = 2
MIN_S = 0.5
SOURCE_MAX_AGE_S = 5.0
RC_WINDOW_S = 1.0
NAV_AFTER_S = 2.0


def _check(ts, vals):
    if len(ts) != len(vals):
        raise ValueError("timestamps and values differ in length: %d vs %d" % (len(ts), len(vals)))
    for i in range(1, len(ts)):
        if ts[i] < ts[i - 1]:
            raise ValueError("timestamps not sorted at index %d" % i)


def intervals_where(ts, vals, pred, t_end):
    """Half-open intervals [a, b) where pred(value) holds, b <= t_end. Adjacent true samples merge."""
    _check(ts, vals)
    out = []
    start = None
    for i, (t, v) in enumerate(zip(ts, vals)):
        if t >= t_end:
            break
        ok = bool(pred(v))
        if ok and start is None:
            start = t
        elif not ok and start is not None:
            if t > start:
                out.append((start, t))
            start = None
    if start is not None and t_end > start:
        out.append((start, t_end))
    return out


def intersect(a_list, b_list):
    """Intersection of two sorted lists of disjoint half-open intervals."""
    out = []
    i = j = 0
    while i < len(a_list) and j < len(b_list):
        lo = max(a_list[i][0], b_list[j][0])
        hi = min(a_list[i][1], b_list[j][1])
        if hi > lo:
            out.append((lo, hi))
        if a_list[i][1] < b_list[j][1]:
            i += 1
        else:
            j += 1
    return out


def total(intervals):
    return sum(b - a for a, b in intervals)


def inside(t, intervals):
    return any(a <= t < b for a, b in intervals)


def airborne_intervals(arm_ts, arm_state, land_ts, landed, t_end):
    armed = intervals_where(arm_ts, arm_state, lambda v: int(v) == ARMED, t_end)
    flying = intervals_where(land_ts, landed, lambda v: not bool(v), t_end)
    return armed, intersect(armed, flying)


def loss_events(fs_ts, lost, airborne, t_end, min_s=MIN_S):
    """In-air loss events: list of dicts {onset, end, duration, cleared}."""
    _check(fs_ts, lost)
    events = []
    prev = None
    i = 0
    n = len(fs_ts)
    while i < n:
        v = bool(lost[i])
        if v and prev is False:
            onset = fs_ts[i]
            j = i + 1
            while j < n and bool(lost[j]):
                j += 1
            end = fs_ts[j] if j < n else t_end
            cleared = j < n
            if inside(onset, airborne) and end - onset >= min_s:
                events.append({"onset": onset, "end": end, "duration": end - onset, "cleared": cleared})
            prev = True if j >= n else False
            i = j + 1 if j < n else n
            continue
        prev = v
        i += 1
    return events


def link_status(fs_ts, lost, armed, t_end):
    """'used' if manual control was present (flag false) for any armed time, 'never' if the flag was true for
    all armed time that has a known value, 'no_flag' if failsafe_flags has no sample, 'not_armed' if no armed time."""
    if not fs_ts:
        return "no_flag"
    if total(armed) <= 0:
        return "not_armed"
    present = intervals_where(fs_ts, lost, lambda v: not bool(v), t_end)
    if total(intersect(present, armed)) > 0:
        return "used"
    return "never"


def value_at(ts, vals, t, max_age=None):
    """Value holding at time t (last sample with ts <= t), or None; None if older than max_age."""
    _check(ts, vals)
    lo, hi = 0, len(ts)
    while lo < hi:
        mid = (lo + hi) // 2
        if ts[mid] <= t:
            lo = mid + 1
        else:
            hi = mid
    k = lo - 1
    if k < 0:
        return None
    if max_age is not None and t - ts[k] > max_age:
        return None
    return vals[k]


def source_class(code):
    if code is None:
        return "none"
    code = int(code)
    if code == 1:
        return "rc"
    if 2 <= code <= 7:
        return "mavlink"
    return "unknown"


def source_before(mcs_ts, data_source, onset, max_age=SOURCE_MAX_AGE_S):
    """Last data_source strictly before the onset (the sample at the onset may already reflect the loss)."""
    _check(mcs_ts, data_source)
    k = None
    for idx, t in enumerate(mcs_ts):
        if t < onset:
            k = idx
        else:
            break
    if k is None or onset - mcs_ts[k] > max_age:
        return source_class(None)
    return source_class(data_source[k])


def rc_agrees(rc_ts, rc_lost, rc_failsafe, onset, end, window=RC_WINDOW_S):
    """None if input_rc was not logged; else True if rc_lost or rc_failsafe is true anywhere in the window."""
    if not rc_ts:
        return None
    _check(rc_ts, rc_lost)
    _check(rc_ts, rc_failsafe)
    a, b = onset - window, end + window

    def bad(k):
        return bool(rc_lost[k]) or bool(rc_failsafe[k])

    k0 = None
    for k, t in enumerate(rc_ts):
        if t <= a:
            k0 = k
        elif t <= b:
            if bad(k):
                return True
        else:
            break
    return bool(k0 is not None and bad(k0))


def nav_after(vs_ts, nav_state, onset, delay=NAV_AFTER_S):
    v = value_at(vs_ts, nav_state, onset + delay)
    return None if v is None else int(v)


def analyse(topics, t_end, min_s=MIN_S):
    """topics: dict of name -> dict of field -> list (each with 'ts'). Missing topics are absent keys.
    Returns the per-log record (no positions, no free text)."""
    vs = topics.get("vehicle_status")
    ld = topics.get("vehicle_land_detected")
    fs = topics.get("failsafe_flags")
    mcs = topics.get("manual_control_setpoint")
    rc = topics.get("input_rc")
    rec = {"has": {k: k in topics for k in ("vehicle_status", "vehicle_land_detected", "failsafe_flags",
                                             "manual_control_setpoint", "input_rc")}}
    if not vs or not ld or fs is None:
        rec["status"] = "missing_topics"
        rec["events"] = []
        return rec
    armed, air = airborne_intervals(vs["ts"], vs["arming_state"], ld["ts"], ld["landed"], t_end)
    rec["armed_s"] = round(total(armed), 3)
    rec["airborne_s"] = round(total(air), 3)
    rec["link"] = link_status(fs["ts"], fs["manual_control_signal_lost"], armed, t_end)
    if mcs:
        srcs = sorted({source_class(c) for c in mcs["data_source"]})
        rec["sources_seen"] = srcs
    else:
        rec["sources_seen"] = []
    events = []
    for ev in loss_events(fs["ts"], fs["manual_control_signal_lost"], air, t_end, min_s):
        row = dict(ev)
        row["source"] = source_before(mcs["ts"], mcs["data_source"], ev["onset"]) if mcs else "none"
        row["rc_agrees"] = (rc_agrees(rc["ts"], rc["rc_lost"], rc["rc_failsafe"], ev["onset"], ev["end"])
                            if rc else None)
        row["nav_after"] = nav_after(vs["ts"], vs["nav_state"], ev["onset"])
        for k in ("onset", "end", "duration"):
            row[k] = round(row[k], 3)
        events.append(row)
    rec["events"] = events
    rec["status"] = "ok"
    return rec

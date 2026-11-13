"""fixscreen 0.1.0: screen the public PX4 flight logs MV2 already downloaded (no new downloads) for real
positive cases of four flight-review-rs analyzers whose maintainers still want a labelled real fixture.

Why: crates/converter/src/diagnostics/CONTRIBUTING.md in PX4/flight-review-rs (Flight Review v2, commit
3600132f707868e3e0157b8204de966af7c435f9) says "Positive field data is still needed for motor commands,
connected low batteries, GPS degradation and RC loss". rc_loss.rs carries SKIP_FIXTURE; battery_brownout.rs,
gps_interference.rs and motor_failure.rs carry NEGATIVE_FIXTURE notes ("A labeled positive ... fixture is
needed"). Their tests want a `detects_real_*` test on a real, independently labelled log.

This is a SCREEN, not the analyzers. Each *_oracle() restates the analyzer's documented rule as read on
2026-11-13 from the source at that commit (summarised, not copied):
  rc_loss          input_rc.rc_lost or rc_failsafe while vehicle_status.arming_state == 2; window closes on
                   recovery, disarm or end of log; reported if >= 0.5 s; critical if >= 5 s.
  battery_brownout battery_status connected, cell_count > 0, voltage_v (else voltage_filtered_v) finite and
                   > 0, armed, voltage < 3.3 V x cell_count; one detection per 30 s (reset by disarm,
                   disconnect or a cell-count change).
  gps_interference vehicle_gps_position: baseline = mean eph and mean satellites_used over the first 10
                   consecutive usable samples (fix_type >= 3 or absent, satellites >= 4, eph > 0, epv > 0);
                   then eph > 5 and > 3 x baseline eph, or epv > 10, or satellites < 0.5 x baseline; 5 s
                   dedup; critical if eph > 10 or satellites < 4. No armed gating.
  motor_failure    actuator_outputs while armed: a channel (index < min(noutputs, 16)) goes from > 0 to
                   exactly 0.0.
The authoritative check is a later run of flight-review-rs itself on the chosen files. Each candidate also
gets context an independent label needs: airborne or not (vehicle_land_detected.landed), mode, how long the
condition lasted, and the firmware's own logged messages around it.

Only reads files under <data dir>; makes no network requests. Byte-identical files are counted once.

Usage: python fixscreen.py <data dir> <out dir>
Apache-2.0. Made with AI agents working in the name of MV2, Munim, Inc. (mv2@munimtech.com).
"""
import bisect
import hashlib
import heapq
import json
import math
import os
import re
import subprocess
import sys
import time

__version__ = "0.1.0"

HERE = os.path.dirname(os.path.abspath(__file__))
ARMED = 2
RC_MIN_US = 500_000
RC_CRITICAL_US = 5_000_000
V_CELL = 3.3
BATT_DEDUP_US = 30_000_000
GPS_BASELINE_N = 10
GPS_SATS_MIN = 4
GPS_EPH_SPIKE = 5.0
GPS_EPH_CRIT = 10.0
GPS_EPV = 10.0
GPS_SATS_DROP = 0.5
GPS_DEDUP_US = 5_000_000
MAX_MOTORS = 16
MAX_BYTES = 100 * 1000 * 1000
TIME_BUDGET_S = 100 * 60
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NAV = {0: "MANUAL", 1: "ALTCTL", 2: "POSCTL", 3: "AUTO_MISSION", 4: "AUTO_LOITER", 5: "AUTO_RTL",
       6: "POSITION_SLOW", 10: "ACRO", 12: "DESCEND", 13: "TERMINATION", 14: "OFFBOARD", 15: "STAB",
       17: "AUTO_TAKEOFF", 18: "AUTO_LAND", 19: "AUTO_FOLLOW_TARGET", 20: "AUTO_PRECLAND", 21: "ORBIT",
       22: "AUTO_VTOL_TAKEOFF"}
RELEASE_TYPES = {0: "-dev", 64: "-alpha", 128: "-beta", 192: "-rc", 255: ""}
TOPICS = ["vehicle_status", "input_rc", "battery_status", "vehicle_gps_position", "actuator_outputs",
          "vehicle_land_detected"]
PARAMS = ("MAV_TYPE", "SYS_AUTOSTART", "BAT1_N_CELLS", "BAT_N_CELLS", "BAT1_V_EMPTY", "BAT_V_EMPTY",
          "COM_RC_LOSS_T", "NAV_RCL_ACT", "CA_ROTOR_COUNT", "SDLOG_PROFILE")
WINDOW_BEFORE_US = 5_000_000
WINDOW_AFTER_US = 10_000_000


# ---------------------------------------------------------------- pure helpers (tested)

def decode_release(v):
    """PX4 ver_sw_release (0xMMmmppTT) -> 'v1.14.0' / 'v1.11.0-beta'; None if not an int."""
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    major, minor, patch, typ = (v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
    return "v%d.%d.%d%s" % (major, minor, patch, RELEASE_TYPES.get(typ, "-type%d" % typ))


def get(cols, field, i):
    """Value of field at row i as a plain Python number, or None if the field is absent."""
    c = cols.get(field)
    if c is None:
        return None
    v = c[i]
    return v.item() if hasattr(v, "item") else v


def nrows(cols):
    return len(cols["timestamp"])


def finite_pos(v):
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v) and v > 0


def merged(status, topic):
    """(timestamp, kind, row) in time order; kind 0 = vehicle_status, 1 = topic. Status first on ties."""
    a = ((int(get(status, "timestamp", i)), 0, i) for i in range(nrows(status))) if status else iter(())
    b = ((int(get(topic, "timestamp", i)), 1, i) for i in range(nrows(topic)))
    return heapq.merge(a, b)


def value_at(ts, vals, t):
    """Last value at or before t (ts sorted), else None."""
    i = bisect.bisect_right(ts, t) - 1
    return vals[i] if i >= 0 else None


def rc_oracle(status, rc):
    """Loss windows per rc_loss's rule. Returns dicts with start, end, dur_us, closed_by, severity."""
    armed, lost, start, last_input, last_ts, out = False, False, None, None, 0, []

    def emit(s, e, why):
        d = max(e - s, 0)
        if d >= RC_MIN_US:
            out.append({"start": s, "end": e, "dur_us": d, "closed_by": why,
                        "severity": "critical" if d >= RC_CRITICAL_US else "warning"})

    for ts, kind, i in merged(status, rc):
        last_ts = max(last_ts, ts)
        if kind == 0:
            a = get(status, "arming_state", i)
            if a is None:
                continue
            was, armed = armed, int(a) == ARMED
            if was and not armed:
                if start is not None:
                    emit(start, ts, "disarm")
                start, lost = None, False
            continue
        if last_input is not None and ts <= last_input:
            continue
        l1, l2 = get(rc, "rc_lost", i), get(rc, "rc_failsafe", i)
        if l1 is None and l2 is None:
            continue
        last_input = ts
        sig_lost = bool(l1) or bool(l2)
        if sig_lost and armed and not lost:
            lost, start = True, ts
        elif not sig_lost and lost:
            s, lost, start = start, False, None
            if armed:
                emit(s, ts, "recovery")
    if lost and armed and start is not None:
        emit(start, max(last_ts, start), "end of log")
    return out


def battery_oracle(status, batt):
    """Detections per battery_brownout's rule, each with how long the pack stayed below the threshold."""
    armed, cells, last_det, last_ts, out = False, None, None, None, []
    open_run = None
    for ts, kind, i in merged(status, batt):
        if kind == 0:
            a = get(status, "arming_state", i)
            if a is None:
                continue
            was, armed = armed, int(a) == ARMED
            if was and not armed:
                last_det = None
                if open_run is not None:
                    open_run["below_until"], open_run["below_end"] = ts, "disarm"
                    open_run = None
            continue
        if last_ts is not None and ts <= last_ts:
            continue
        last_ts = ts
        conn = get(batt, "connected", i)
        c = get(batt, "cell_count", i)
        v = get(batt, "voltage_v", i)
        if not finite_pos(v):
            v = get(batt, "voltage_filtered_v", i)
            v = v if finite_pos(v) else None
        ok = conn is not None and bool(conn) and c is not None and int(c) > 0 and v is not None
        below = ok and armed and v < int(c) * V_CELL
        if open_run is not None and not below:
            open_run["below_until"], open_run["below_end"] = ts, "recovered" if ok and armed else "sample unusable"
            open_run = None
        elif open_run is not None:
            open_run["min_v"] = min(open_run["min_v"], v)
            open_run["below_until"] = ts
        if conn is None or not bool(conn):
            cells, last_det = None, None
            continue
        if c is None or int(c) == 0:
            cells = None
            continue
        if v is None:
            continue
        c = int(c)
        if cells != c:
            last_det, cells = None, c
        if not armed:
            continue
        if last_det is not None and ts - last_det < BATT_DEDUP_US:
            continue
        if v < c * V_CELL:
            cur = get(batt, "current_a", i)
            det = {"t": ts, "voltage_v": v, "cells": c, "threshold_v": round(c * V_CELL, 4),
                   "v_per_cell": round(v / c, 4), "current_a": cur if cur is not None and math.isfinite(cur) else None,
                   "min_v": v, "below_until": ts, "below_end": "end of log"}
            if open_run is not None:
                open_run["below_end"] = "next detection"
            out.append(det)
            last_det = ts
            open_run = det
    for d in out:
        d["below_us"] = d["below_until"] - d["t"]
    return out


def gps_oracle(gps):
    """Detections per gps_interference's rule. Returns dicts with t, reasons, severity, baseline."""
    n, s_eph, s_sats, base, last_ts, last_det, out = 0, 0.0, 0.0, None, None, None, []
    for i in range(nrows(gps)):
        ts = int(get(gps, "timestamp", i))
        if last_ts is not None and ts <= last_ts:
            continue
        last_ts = ts
        eph, epv, sats = get(gps, "eph", i), get(gps, "epv", i), get(gps, "satellites_used", i)
        if eph is None or epv is None or sats is None:
            continue
        if not (math.isfinite(eph) and math.isfinite(epv)) or eph < 0 or epv < 0:
            continue
        sats = int(sats)
        if base is None:
            fix = get(gps, "fix_type", i)
            usable = (fix is None or int(fix) >= 3) and sats >= GPS_SATS_MIN and eph > 0 and epv > 0
            if not usable:
                n, s_eph, s_sats = 0, 0.0, 0.0
                continue
            n, s_eph, s_sats = n + 1, s_eph + eph, s_sats + sats
            if n == GPS_BASELINE_N:
                base = (s_eph / n, s_sats / n)
            continue
        reasons = []
        if eph > GPS_EPH_SPIKE and eph > 3 * base[0]:
            reasons.append("eph_spike")
        if epv > GPS_EPV:
            reasons.append("epv")
        if base[1] > 0 and sats < base[1] * (1 - GPS_SATS_DROP):
            reasons.append("satellite_drop")
        if not reasons:
            continue
        if last_det is not None and ts - last_det < GPS_DEDUP_US:
            continue
        last_det = ts
        out.append({"t": ts, "reasons": reasons, "eph": round(eph, 3), "epv": round(epv, 3), "sats": sats,
                    "severity": "critical" if eph > GPS_EPH_CRIT or sats < GPS_SATS_MIN else "warning",
                    "baseline_eph": round(base[0], 3), "baseline_sats": round(base[1], 2)})
    return out


def motor_oracle(status, outs):
    """Detections per motor_failure's rule: channel drops from > 0 to exactly 0.0 while armed."""
    armed, nav, prev, last_ts, out = False, None, {}, None, []
    for ts, kind, i in merged(status, outs):
        if kind == 0:
            a = get(status, "arming_state", i)
            ns = get(status, "nav_state", i)
            if ns is not None:
                nav = int(ns)
            if a is None:
                continue
            armed = int(a) == ARMED
            if not armed:
                prev = {}
            continue
        if not armed:
            continue
        if last_ts is not None and ts <= last_ts:
            continue
        last_ts = ts
        nout = get(outs, "noutputs", i)
        if nout is not None:
            count = min(int(nout), MAX_MOTORS)
        else:
            count = 0
            while count < MAX_MOTORS and ("output[%d]" % count) in outs:
                count += 1
        for k in [k for k in prev if k >= count]:
            del prev[k]
        for ch in range(count):
            v = get(outs, "output[%d]" % ch, i)
            if v is None or not math.isfinite(v):
                prev.pop(ch, None)
                continue
            p = prev.get(ch)
            if v == 0.0 and p is not None and p > 0:
                out.append({"t": ts, "channel": ch, "previous": p, "nav_state": nav})
            prev[ch] = v
    return out


def zero_run_us(outs, ch, t):
    """How long channel ch stays exactly 0.0 from time t (until the first nonzero sample or the last zero)."""
    ts = outs["timestamp"]
    i = bisect.bisect_left([int(x) for x in ts], t)
    end = t
    for k in range(i, len(ts)):
        v = get(outs, "output[%d]" % ch, k)
        if v is None or v != 0.0:
            return int(get(outs, "timestamp", k)) - t
        end = int(get(outs, "timestamp", k))
    return end - t


def fr_time(rel_s):
    m = int(rel_s // 60)
    return "%d:%05.2f" % (m, rel_s - 60 * m)


# ---------------------------------------------------------------- log reading

def columns(u, topic, fields=None):
    """{multi_id: {field: numpy column}} for a topic (all fields if fields is None)."""
    res = {}
    for d in u.data_list:
        if d.name == topic:
            res[d.multi_id] = {k: v for k, v in d.data.items() if fields is None or k in fields or k == "timestamp"}
    return res


def ctx_series(u):
    land = columns(u, "vehicle_land_detected", ("landed",))
    st = columns(u, "vehicle_status", ("arming_state", "nav_state"))
    s = {}
    if land:
        c = land[min(land)]
        s["landed"] = ([int(x) for x in c["timestamp"]], [int(x) for x in c["landed"]] if "landed" in c else None)
    if st:
        c = st[min(st)]
        if "nav_state" in c:
            s["nav"] = ([int(x) for x in c["timestamp"]], [int(x) for x in c["nav_state"]])
    return s


def context(series, msgs, t0, t):
    land = series.get("landed")
    landed = value_at(land[0], land[1], t) if land and land[1] is not None else None
    nav = series.get("nav")
    ns = value_at(nav[0], nav[1], t) if nav else None
    near = [m for m in msgs if t - WINDOW_BEFORE_US <= m[0] <= t + WINDOW_AFTER_US][:8]
    return {"rel_s": round((t - t0) / 1e6, 3), "fr_time": fr_time((t - t0) / 1e6),
            "airborne": None if landed is None else landed == 0, "mode": NAV.get(ns, ns),
            "messages": [[fr_time((m[0] - t0) / 1e6), m[1], m[2]] for m in near]}


def scan(path):
    from pyulog import ULog
    u = ULog(path, TOPICS, True)
    info = u.msg_info_dict
    t0 = u.start_timestamp
    rec = {"sys_name": info.get("sys_name"), "ver_hw": info.get("ver_hw"),
           "release": decode_release(info.get("ver_sw_release")),
           "duration_s": round((u.last_timestamp - t0) / 1e6, 1)}
    initial = getattr(u, "initial_parameters", None) or {}
    rec["params"] = {p: initial.get(p) for p in PARAMS if p in initial}
    msgs = [(int(m.timestamp), int(m.log_level), (m.message or "")[:140]) for m in (u.logged_messages or [])]
    series = ctx_series(u)
    status_all = columns(u, "vehicle_status", ("arming_state", "nav_state"))
    status = status_all[min(status_all)] if status_all else None
    found = {}
    if status is not None:
        for mid, c in sorted(columns(u, "input_rc", ("rc_lost", "rc_failsafe")).items()):
            for w in rc_oracle(status, c):
                w.update(context(series, msgs, t0, w["start"]), instance=mid)
                w["end_fr"] = fr_time((w["end"] - t0) / 1e6)
                found.setdefault("rc_loss", []).append(w)
        for mid, c in sorted(columns(u, "battery_status", ("connected", "cell_count", "voltage_v", "voltage_filtered_v",
                                                           "current_a")).items()):
            for d in battery_oracle(status, c):
                d.update(context(series, msgs, t0, d["t"]), instance=mid)
                found.setdefault("battery_brownout", []).append(d)
        for mid, c in sorted(columns(u, "actuator_outputs").items()):
            for d in motor_oracle(status, c):
                d.update(context(series, msgs, t0, d["t"]), instance=mid)
                d["zero_for_us"] = zero_run_us(c, d["channel"], d["t"])
                found.setdefault("motor_failure", []).append(d)
                if len(found["motor_failure"]) >= 50:
                    break
    for mid, c in sorted(columns(u, "vehicle_gps_position", ("eph", "epv", "satellites_used", "fix_type")).items()):
        for d in gps_oracle(c)[:50]:
            d.update(context(series, msgs, t0, d["t"]), instance=mid)
            found.setdefault("gps_interference", []).append(d)
    rec["topics"] = sorted({d.name for d in u.data_list})
    rec["found"] = found
    rec["counts"] = {k: len(v) for k, v in found.items()}
    return rec


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_ulog(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"ULog"
    except OSError:
        return False


def inventory(data):
    files = []
    for dirpath, dirnames, filenames in os.walk(data):
        dirnames.sort()
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            if name.endswith(".ulg") or (os.path.splitext(name)[1] == "" and is_ulog(p)):
                files.append(p)
    return files


# ---------------------------------------------------------------- ranking

def score(cls, d, bytes_):
    """Sort key: airborne first, then a substantial event, then smaller file."""
    air = 0 if d.get("airborne") else 1
    if cls == "rc_loss":
        strong = 0 if d["dur_us"] >= 1_000_000 else 1
    elif cls == "battery_brownout":
        strong = 0 if d["below_us"] >= 1_000_000 else 1
    elif cls == "gps_interference":
        strong = 0 if ("eph_spike" in d["reasons"] or "satellite_drop" in d["reasons"]) else 1
    else:
        strong = 0 if d.get("zero_for_us", 0) >= 1_000_000 else 1
    return (air, strong, bytes_ or 0)


def run_tests(out, res):
    p = subprocess.run([sys.executable, "-m", "unittest", "-v", "test_fixscreen"], cwd=HERE, capture_output=True,
                       text=True)
    with open(os.path.join(out, "tests.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(p.stdout + p.stderr)
    res["tests_exit"] = p.returncode
    assert p.returncode == 0, "tests failed: %s" % (p.stdout + p.stderr)[-1500:]


def main(data, out):
    os.makedirs(out, exist_ok=True)
    t_start = time.time()
    res = {"fixscreen": __version__, "ok": False, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "note": "screen only; the authoritative check is flight-review-rs's own analyzers on the chosen files",
           "frrs_commit": "3600132f707868e3e0157b8204de966af7c435f9"}
    rows = []
    try:
        run_tests(out, res)
        import pyulog
        res["pyulog"] = getattr(pyulog, "__version__", "?")
        files = inventory(data)
        res["files_found"] = len(files)
        folders = {}
        for p in files:
            k = os.path.relpath(os.path.dirname(p), data)
            folders[k] = folders.get(k, 0) + 1
        res["folders"] = folders
        seen = {}
        for p in files:
            if time.time() - t_start > TIME_BUDGET_S:
                res["stopped"] = "time budget"
                break
            stem = os.path.splitext(os.path.basename(p))[0]
            row = {"path": os.path.relpath(p, data), "log_id": stem if UUID_RE.match(stem) else None,
                   "bytes": os.path.getsize(p)}
            if row["bytes"] > MAX_BYTES or not is_ulog(p):
                row["skipped"] = "too large" if row["bytes"] > MAX_BYTES else "not a ULog"
                rows.append(row)
                continue
            row["sha256"] = sha_file(p)
            if row["sha256"] in seen:
                row["duplicate_of"] = seen[row["sha256"]]
                rows.append(row)
                continue
            seen[row["sha256"]] = row["path"]
            try:
                row.update(scan(p))
            except Exception as exc:
                row["scan_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
            rows.append(row)
        scanned = [r for r in rows if "found" in r]
        res["scanned_distinct"] = len(scanned)
        res["duplicates"] = sum(1 for r in rows if "duplicate_of" in r)
        res["scan_errors"] = sum(1 for r in rows if "scan_error" in r)
        summary = {}
        for cls in ("rc_loss", "battery_brownout", "gps_interference", "motor_failure"):
            cands = []
            for r in scanned:
                for d in r["found"].get(cls, []):
                    cands.append((score(cls, d, r["bytes"]), r, d))
            cands.sort(key=lambda x: x[0])
            logs_with = len({id(r) for _, r, _ in cands})
            logs_air = len({id(r) for _, r, d in cands if d.get("airborne")})
            summary[cls] = {"logs_with_any": logs_with, "logs_with_airborne": logs_air,
                            "top": [{"log_id": r["log_id"], "path": r["path"], "bytes": r["bytes"],
                                     "release": r.get("release"), "sys_name": r.get("sys_name"),
                                     "ver_hw": r.get("ver_hw"), "mav_type": (r.get("params") or {}).get("MAV_TYPE"),
                                     "event": {k: v for k, v in d.items() if k not in ("messages",)},
                                     "messages": d.get("messages")} for _, r, d in cands[:12]]}
        res["summary"] = summary
        res["ok"] = True
    except Exception as exc:
        import traceback
        res["error"] = traceback.format_exc()[-2000:]
    finally:
        with open(os.path.join(out, "fixscreen-rows.jsonl"), "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(os.path.join(out, "fixscreen.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(res, f, indent=1)
        print(json.dumps({k: res.get(k) for k in ("ok", "files_found", "scanned_distinct", "duplicates",
                                                   "scan_errors", "folders", "error")}, indent=1), flush=True)
        for cls, s in (res.get("summary") or {}).items():
            print(cls, s["logs_with_any"], s["logs_with_airborne"],
                  [(t["log_id"], t["bytes"], t["event"].get("fr_time"), t["event"].get("airborne")) for t in s["top"][:3]],
                  flush=True)
    if not res["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/data", sys.argv[2] if len(sys.argv) > 2 else "/out")

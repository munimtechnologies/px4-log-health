"""mcloss_census.py: px4-log-health studies/mcloss-2026 (in-flight manual-control loss in public PX4 logs).

Pre-registered in studies/mcloss-2026/PLAN.md. Before it reads any log it
1. runs the synthetic tests of mcloss_core.py and of this file (stops if any fails);
2. checks that PLAN.md, mcloss_census.py and mcloss_core.py on px4-log-health main are the copies it runs
   (waits up to 90 min for them).
Then it streams review.px4.io/dbinfo once, keeps public real-hardware logs of PX4 v1.14 or newer that are
120 s or longer (any vehicle type), and builds two samples with at most one log per vehicle:
  R (random)    each vehicle's log with the smallest sha256(log_id); vehicles in order of that hash;
  E (enriched)  the same, among logs whose Flight Review mode list has a manual mode (0, 1, 2, 6, 10, 15)
                and also RTL (5), LAND (18) or DESCEND (12).
It walks R and E in the pattern R, R, R, E, downloads each log (6 s apart, 50 MB or less, stops on HTTP
403/429/444), drops byte-identical re-uploads (same sha256) before counting, reads six topics with pyulog
and runs mcloss_core.analyse. It stops when R has 260 and E has 80 counted logs, after 360 downloads or
after 240 minutes. Logs are deleted after they are read; no position, GPS track or description is read.

Writes rows-mcloss.jsonl, summary-mcloss.json and job-fetched-mcloss.json to OUT.

    python mcloss_census.py /out

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import gc
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.request

import mcloss_core as core

__version__ = "0.1.0"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "munimtechnologies/px4-log-health"
API_MAIN = "https://api.github.com/repos/%s/commits/main"
RAW = "https://raw.githubusercontent.com/%s/%s/%s"
PUBLISHED = {"studies/mcloss-2026/PLAN.md": "PLAN.md", "studies/mcloss-2026/mcloss_census.py": "mcloss_census.py",
             "studies/mcloss-2026/mcloss_core.py": "mcloss_core.py"}
DBINFO = "https://review.px4.io/dbinfo"
DOWNLOAD = "https://review.px4.io/download?log=%s"
UA = ("px4-log-health-mcloss/%s (MV2 of Munim, Inc.; mv2@munimtech.com; research on public logs)" % __version__)
WAIT_MIN = 90
MIN_DURATION_S = 120.0
MIN_SERIES = (1, 14)
MAX_BYTES = 50 * 1000 * 1000
R_TARGET = 260
E_TARGET = 80
MAX_DOWNLOADS = 360
BUDGET_MIN = 240
DELAY_S = 6.0
PATTERN = ("R", "R", "R", "E")
MANUAL_MODES = frozenset((0, 1, 2, 6, 10, 15))
RECOVERY_MODES = frozenset((5, 12, 18))
MIN_AIRBORNE_S = 10.0
TOPICS = {
    "vehicle_status": ("arming_state", "nav_state"),
    "vehicle_land_detected": ("landed",),
    "failsafe_flags": ("manual_control_signal_lost",),
    "manual_control_setpoint": ("data_source",),
    "input_rc": ("rc_lost", "rc_failsafe"),
}
PARAMS = ("COM_RC_IN_MODE", "NAV_RCL_ACT", "COM_RC_LOSS_T", "COM_RCL_EXCEPT")
NAV_NAMES = {0: "MANUAL", 1: "ALTCTL", 2: "POSCTL", 3: "AUTO_MISSION", 4: "AUTO_LOITER", 5: "AUTO_RTL",
             6: "POSITION_SLOW", 10: "ACRO", 12: "DESCEND", 13: "TERMINATION", 14: "OFFBOARD", 15: "STAB",
             17: "AUTO_TAKEOFF", 18: "AUTO_LAND", 19: "AUTO_FOLLOW_TARGET", 20: "AUTO_PRECLAND", 21: "ORBIT",
             22: "AUTO_VTOL_TAKEOFF"}


class Blocked(Exception):
    pass


# ---------------------------------------------------------------- selection (pure, tested)

def release_series(value):
    """((major, minor), kind) from Flight Review's ver_sw_release ('v1.16.0 255'), or None."""
    m = re.match(r"\s*v?(\d+)\.(\d+)\.(\d+)\s*\(?\s*([A-Za-z0-9]*)", str(value or ""))
    if not m:
        return None
    major, minor, _patch, kind = m.groups()
    k = kind.lower()
    if k in ("0", "dev", "development"):
        kind = "dev"
    elif k in ("255", "release", "stable"):
        kind = "release"
    elif k.isdigit() or k in ("alpha", "beta", "rc", "candidate"):
        kind = "pre"
    else:
        kind = "unknown"
    return (int(major), int(minor)), kind


def vehicle_hash(uuid):
    return hashlib.sha256(str(uuid).encode()).hexdigest()[:12] if uuid else None


def placeholder_uuid(uuid):
    s = str(uuid or "").lower()
    return len(s) < 8 or set(s) <= set("0f-")


def key_of(log_id):
    return hashlib.sha256(str(log_id).encode()).hexdigest()


def modes_of(entry):
    out = set()
    for x in entry.get("flight_modes") or []:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            pass
    return sorted(out)


def is_enriched(modes):
    s = set(modes)
    return bool(s & MANUAL_MODES) and bool(s & RECOVERY_MODES)


def compact(entry):
    """The fields this study keeps of one dbinfo entry, or None if the log is not eligible."""
    if "SITL" in str(entry.get("sys_hw") or "").upper():
        return None
    if "simul" in str(entry.get("type") or "").lower():
        return None
    if str(entry.get("source") or "").upper() == "CI":
        return None
    if not entry.get("log_id") or placeholder_uuid(entry.get("vehicle_uuid")):
        return None
    try:
        duration = float(entry.get("duration_s") or 0)
    except (TypeError, ValueError):
        return None
    if duration < MIN_DURATION_S:
        return None
    rs = release_series(entry.get("ver_sw_release"))
    if rs is None or rs[0] < MIN_SERIES:
        return None
    modes = modes_of(entry)
    lid = str(entry["log_id"])
    return {"log_id": lid, "key": key_of(lid), "v": vehicle_hash(entry.get("vehicle_uuid")),
            "rel": str(entry.get("ver_sw_release") or ""), "hw": str(entry.get("sys_hw") or ""),
            "mav": str(entry.get("mav_type") or ""), "dur": int(duration), "modes": modes,
            "E": is_enriched(modes)}


def pick(entries, enriched_only=False):
    """One log per vehicle (smallest key), the picks in order of key."""
    best = {}
    for e in entries:
        if enriched_only and not e["E"]:
            continue
        b = best.get(e["v"])
        if b is None or e["key"] < b["key"]:
            best[e["v"]] = e
    return sorted(best.values(), key=lambda e: e["key"])


def plan_order(r_list, e_list, pattern=PATTERN):
    """[(stratum, entry)] interleaving R and E in the given pattern until both lists are used up."""
    out, i, j = [], 0, 0
    while i < len(r_list) or j < len(e_list):
        for s in pattern:
            if s == "R" and i < len(r_list):
                out.append(("R", r_list[i]))
                i += 1
            elif s == "E" and j < len(e_list):
                out.append(("E", e_list[j]))
                j += 1
    return out


# ---------------------------------------------------------------- extraction (pure given a ULog-like object)

def _list(x):
    return x.tolist() if hasattr(x, "tolist") else list(x)


def extract(ulog):
    """(topics for mcloss_core.analyse, t_end in s, meta). Uses each topic's lowest multi_id."""
    chosen, instances = {}, {}
    for d in ulog.data_list:
        if d.name not in TOPICS:
            continue
        instances[d.name] = instances.get(d.name, 0) + 1
        if d.name not in chosen or d.multi_id < chosen[d.name].multi_id:
            chosen[d.name] = d
    topics, missing_fields, resorted = {}, {}, []
    for name, d in sorted(chosen.items()):
        fields = TOPICS[name]
        miss = [f for f in ("timestamp",) + fields if f not in d.data]
        if miss:
            missing_fields[name] = miss
            continue
        ts = [t / 1e6 for t in _list(d.data["timestamp"])]
        cols = {f: [int(v) for v in _list(d.data[f])] for f in fields}
        if any(ts[k] < ts[k - 1] for k in range(1, len(ts))):
            order = sorted(range(len(ts)), key=lambda k: ts[k])
            ts = [ts[k] for k in order]
            cols = {f: [c[k] for k in order] for f, c in cols.items()}
            resorted.append(name)
        rec = {"ts": ts}
        rec.update(cols)
        topics[name] = rec
    params = {}
    initial = getattr(ulog, "initial_parameters", None) or {}
    for p in PARAMS:
        v = initial.get(p)
        params[p] = None if v is None else (int(v) if float(v).is_integer() else float(v))
    meta = {"instances": instances, "missing_fields": missing_fields, "resorted": resorted, "params": params}
    return topics, ulog.last_timestamp / 1e6, meta


# ---------------------------------------------------------------- grading (pure, tested)

def counted(r):
    return r.get("download") == "ok" and not r.get("duplicate_of") and isinstance(r.get("rec"), dict)


def flown(r):
    rec = r["rec"]
    return (rec.get("status") == "ok" and rec.get("link") == "used"
            and (rec.get("airborne_s") or 0) >= MIN_AIRBORNE_S)


def _share(k, n):
    return round(k / n, 4) if n else None


def band(k, n, low, high, need):
    share = _share(k, n)
    if share is None or n < need:
        return {"result": "too few", "k": k, "n": n, "need": need, "share": share}
    ok = low - 1e-12 <= k / n <= high + 1e-12
    return {"result": "held" if ok else "failed", "k": k, "n": n, "share": share, "low": low, "high": high}


def at_least(k, n, low, need):
    share = _share(k, n)
    if share is None or n < need:
        return {"result": "too few", "k": k, "n": n, "need": need, "share": share}
    return {"result": "held" if k / n >= low - 1e-12 else "failed", "k": k, "n": n, "share": share, "low": low}


def event_logs(rows):
    return [r for r in rows if counted(r) and r["rec"].get("status") == "ok" and r["rec"].get("events")]


def grade(rows):
    R = [r for r in rows if r.get("in_R") and counted(r)]
    fl = [r for r in R if flown(r)]
    k1 = sum(1 for r in fl if r["rec"]["events"])
    g = {"P1_in_air_loss_share_R": band(k1, len(fl), 0.01, 0.10, 150)}
    first = [r["rec"]["events"][0] for r in event_logs(rows)]
    known = [e for e in first if e.get("source") in ("rc", "mavlink")]
    mav = [e for e in known if e["source"] == "mavlink"]
    g["P2_mavlink_share"] = at_least(len(mav), len(known), 0.20, 10)
    g["P3a_mavlink_no_input_rc_signal"] = at_least(sum(1 for e in mav if e.get("rc_agrees") is not True),
                                                   len(mav), 0.80, 6)
    rc_ev = [e for e in known if e["source"] == "rc" and e.get("rc_agrees") is not None]
    g["P4a_rc_input_rc_agrees"] = at_least(sum(1 for e in rc_ev if e.get("rc_agrees") is True), len(rc_ev), 0.70, 6)
    return g


def tally(values):
    out = {}
    for v in values:
        k = json.dumps(v) if isinstance(v, (list, dict)) else str(v)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def describe(rows):
    R = [r for r in rows if r.get("in_R") and counted(r)]
    E = [r for r in rows if r.get("in_E") and counted(r)]
    ok_R = [r for r in R if r["rec"].get("status") == "ok"]
    ev = event_logs(rows)
    firsts = [r["rec"]["events"][0] for r in ev]
    return {
        "R_counted": len(R), "E_counted": len(E),
        "R_status": tally(r["rec"].get("status") for r in R),
        "R_link": tally(r["rec"].get("link") for r in ok_R),
        "R_flown": sum(1 for r in R if flown(r)),
        "R_sources_seen": tally(r["rec"].get("sources_seen") for r in ok_R),
        "R_has_input_rc": sum(1 for r in ok_R if r["rec"]["has"].get("input_rc")),
        "R_com_rc_in_mode": tally((r.get("meta") or {}).get("params", {}).get("COM_RC_IN_MODE") for r in ok_R),
        "E_flown": sum(1 for r in E if flown(r)),
        "E_logs_with_event": sum(1 for r in E if r["rec"].get("status") == "ok" and r["rec"].get("events")),
        "event_logs": len(ev),
        "first_event_source": tally(e.get("source") for e in firsts),
        "first_event_rc_agrees": tally(e.get("rc_agrees") for e in firsts),
        "first_event_nav_after": tally(NAV_NAMES.get(e.get("nav_after"), e.get("nav_after")) for e in firsts),
        "first_event_cleared": tally(e.get("cleared") for e in firsts),
        "events_total": sum(len(r["rec"]["events"]) for r in ev),
        "event_logs_by_mav_type": tally(r.get("mav") for r in ev),
    }


def candidates(rows):
    out = []
    for r in event_logs(rows):
        e = r["rec"]["events"][0]
        out.append({"log_id": r["log_id"], "url": "https://review.px4.io/plot_app?log=%s" % r["log_id"],
                    "in_R": bool(r.get("in_R")), "in_E": bool(r.get("in_E")), "mav": r.get("mav"),
                    "rel": r.get("rel"), "hw": r.get("hw"), "n_events": len(r["rec"]["events"]),
                    "first": {"onset_s": e["onset"], "duration_s": e["duration"], "cleared": e["cleared"],
                              "source": e["source"], "rc_agrees": e["rc_agrees"],
                              "nav_after": NAV_NAMES.get(e["nav_after"], e["nav_after"])},
                    "has_input_rc": r["rec"]["has"].get("input_rc"),
                    "params": (r.get("meta") or {}).get("params")})
    return out


# ---------------------------------------------------------------- network

def get(url, timeout=600):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def norm(data):
    return data.replace(b"\r\n", b"\n")


def wait_for_plan(res):
    """The px4-log-health main commit whose published files equal the local copies."""
    t0 = time.time()
    while True:
        try:
            head = json.loads(get(API_MAIN % REPO, 60))["sha"]
            diffs = {}
            for path, local in PUBLISHED.items():
                with open(os.path.join(HERE, local), "rb") as f:
                    mine = norm(f.read())
                try:
                    theirs = norm(get(RAW % (REPO, head, path), 120))
                except Exception as exc:
                    diffs[path] = "missing (%s)" % str(exc)[:80]
                    continue
                if theirs != mine:
                    diffs[path] = "differs"
            if not diffs:
                res["plan_commit"] = head
                res["published_sha256"] = {p: sha(norm(open(os.path.join(HERE, l), "rb").read()))
                                           for p, l in PUBLISHED.items()}
                return head
            res["last_diffs"] = diffs
        except Exception as exc:
            res["last_wait_error"] = str(exc)[:300]
        if time.time() - t0 > WAIT_MIN * 60:
            raise RuntimeError("the plan and code are not on main after %d min: %s" % (WAIT_MIN, res.get("last_diffs")))
        time.sleep(300)


def download(session, log_id, path, max_bytes):
    """'ok', 'missing', 'too large', 'not a ULog' or 'failed'. Raises Blocked on 403/429/444."""
    import requests
    url = DOWNLOAD % log_id
    for attempt in range(5):
        try:
            n = 0
            with session.get(url, stream=True, timeout=600) as r:
                if r.status_code in (403, 429, 444):
                    raise Blocked("HTTP %d" % r.status_code)
                if r.status_code == 404:
                    return "missing"
                if r.status_code == 503:
                    try:
                        wait = float(r.headers.get("Retry-After"))
                    except (TypeError, ValueError):
                        wait = min(30 * 2 ** attempt, 300)
                    time.sleep(wait)
                    continue
                if r.status_code != 200:
                    time.sleep(10)
                    continue
                if int(r.headers.get("Content-Length") or 0) > max_bytes:
                    return "too large"
                with open(path, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        n += len(chunk)
                        if n > max_bytes:
                            break
                        f.write(chunk)
            if n > max_bytes:
                os.remove(path)
                return "too large"
            with open(path, "rb") as f:
                head = f.read(4)
            if head[:2] == b"\x1f\x8b":
                with gzip.open(path, "rb") as f:
                    data = f.read(max_bytes + 1)
                if len(data) > max_bytes:
                    os.remove(path)
                    return "too large"
                with open(path, "wb") as f:
                    f.write(data)
                head = data[:4]
            if head != b"ULog":
                os.remove(path)
                return "not a ULog"
            return "ok"
        except requests.RequestException:
            time.sleep(10)
    return "failed"


def stream_dbinfo(session, res):
    import ijson
    kept, seen, with_modes = [], 0, 0
    with session.get(DBINFO, stream=True, timeout=900) as resp:
        resp.raise_for_status()
        resp.raw.decode_content = True
        for entry in ijson.items(resp.raw, "item"):
            seen += 1
            if entry.get("flight_modes"):
                with_modes += 1
            c = compact(entry)
            if c:
                kept.append(c)
    res["dbinfo"] = {"entries": seen, "with_flight_modes": with_modes, "eligible": len(kept),
                     "eligible_enriched": sum(1 for c in kept if c["E"])}
    return kept


def measure(session, entry, folder, seen_sha):
    from pyulog import ULog
    log_id = entry["log_id"]
    path = os.path.join(folder, log_id + ".ulg")
    row = {k: entry[k] for k in ("log_id", "v", "mav", "rel", "hw", "dur", "modes")}
    row["download"] = download(session, log_id, path, MAX_BYTES)
    if row["download"] != "ok":
        return row
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        row["bytes"], row["sha256"] = os.path.getsize(path), h.hexdigest()
        if row["sha256"] in seen_sha:
            row["duplicate_of"] = seen_sha[row["sha256"]]
            return row
        seen_sha[row["sha256"]] = log_id
        ulog = ULog(path, list(TOPICS))
        topics, t_end, meta = extract(ulog)
        del ulog
        row["meta"] = meta
        row["rec"] = core.analyse(topics, t_end)
    except Exception as exc:
        row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    finally:
        if os.path.exists(path):
            os.remove(path)
        gc.collect()
    return row


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def run_tests(out, res):
    p = subprocess.run([sys.executable, "-m", "unittest", "-v", "test_mcloss_core", "test_mcloss_census"],
                       cwd=HERE, capture_output=True, text=True)
    with open(os.path.join(out, "tests.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(p.stdout + p.stderr)
    res["tests_exit"] = p.returncode
    assert p.returncode == 0, "tests failed: %s" % (p.stdout + p.stderr)[-1500:]


def main(out):
    os.makedirs(out, exist_ok=True)
    res = {"mcloss_census": __version__, "mcloss_core": core.__version__, "ok": False,
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    rows, by_id = [], {}
    rows_path = os.path.join(out, "rows-mcloss.jsonl")
    try:
        run_tests(out, res)
        wait_for_plan(res)
        import pyulog
        import requests
        res["pyulog"] = getattr(pyulog, "__version__", "?")
        session = requests.Session()
        session.headers["User-Agent"] = UA
        entries = stream_dbinfo(session, res)
        r_list, e_list = pick(entries), pick(entries, enriched_only=True)
        del entries
        gc.collect()
        res["R_listed"], res["E_listed"] = len(r_list), len(e_list)
        order = plan_order(r_list, e_list)
        del r_list, e_list
        folder = "/tmp/logs"
        os.makedirs(folder, exist_ok=True)
        seen_sha, downloads, t0, stop = {}, 0, time.time(), "lists exhausted"
        count = {"R": 0, "E": 0}
        target = {"R": R_TARGET, "E": E_TARGET}
        for s, entry in order:
            if count["R"] >= R_TARGET and count["E"] >= E_TARGET:
                stop = "targets reached"
                break
            if count[s] >= target[s]:
                continue
            lid = entry["log_id"]
            if lid in by_id:
                row = by_id[lid]
                row["in_" + s] = True
                if counted(row):
                    count[s] += 1
                continue
            if downloads >= MAX_DOWNLOADS:
                stop = "download cap"
                break
            if time.time() - t0 > BUDGET_MIN * 60:
                stop = "time budget"
                break
            if downloads:
                time.sleep(DELAY_S)
            downloads += 1
            try:
                row = measure(session, entry, folder, seen_sha)
            except Blocked as exc:
                stop = "blocked: %s" % exc
                break
            row["in_" + s] = True
            row["order"] = downloads
            rows.append(row)
            by_id[lid] = row
            if counted(row):
                count[s] += 1
            if downloads % 10 == 0:
                write_jsonl(rows_path, rows)
        res["stop"], res["downloads"], res["counted"] = stop, downloads, count
        res["minutes"] = round((time.time() - t0) / 60, 1)
        res["ok"] = True
    except Exception:
        res["error"] = traceback.format_exc()[-3000:]
        print(res["error"], flush=True)
    write_jsonl(rows_path, rows)
    summary = {"mcloss_census": __version__, "downloads": tally(r.get("download") for r in rows),
               "duplicates": sum(1 for r in rows if r.get("duplicate_of")),
               "errors": sum(1 for r in rows if r.get("error"))}
    try:
        summary["describe"] = describe(rows)
        summary["grade"] = grade(rows)
        summary["candidates"] = candidates(rows)
    except Exception:
        summary["grade_error"] = traceback.format_exc()[-2000:]
    with open(os.path.join(out, "summary-mcloss.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, indent=1)
        f.write("\n")
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(os.path.join(out, "job-fetched-mcloss.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=1)
        f.write("\n")
    print(json.dumps({"res": res, "grade": summary.get("grade"), "describe": summary.get("describe")},
                     indent=1)[:8000], flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/out"))

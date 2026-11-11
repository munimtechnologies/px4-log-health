"""pairs.py: px4-log-health studies/compare-2026.

How often do compare.py's rules fire on two public flights of one PX4 vehicle
when nothing was changed between them (the false-alarm rate of a before/after
comparison), and what do they say on the upgrade-2026 pairs, where the
firmware and mostly the u-blox dynamic model did change?

Pre-registered in studies/compare-2026/PLAN.md. Before it reads any log it
1. checks that PLAN.md, pairs.py, compare.py and test_compare.py on
   px4-log-health main are the copies it runs (waits up to 90 min for them);
2. downloads px4-log-health v0.3.9 (the checks compare.py compares) and runs
   its fetch_deps.py (px4-gps-lag's gps_lag.py and innov.py, pinned).
Then
3. upgrade pairs: the 11 pairs of px4-gps-lag studies/upgrade-2026;
4. null pairs: streams review.px4.io/dbinfo and keeps public multicopter logs
   from real hardware, PX4 v1.13 or newer, 180 s or longer. For each vehicle,
   in order of the sha256 of its id, it takes the most recent two logs in
   upload order with the same firmware (ver_sw and ver_sw_release), hardware
   and airframe, uploaded at most 7 days apart. It downloads both (50 MB or
   less), runs checkup 0.3.9 and compare.py, and stops at 40 null pairs
   (both logs read, no setting changed), 220 downloads or 200 minutes.
Writes pairs-rows.jsonl, summary.json and job-fetched.json to OUT. Logs are
deleted after they are read; no position is read or written.

    python pairs.py /out

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import gc
import gzip
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import tarfile
import time
import traceback
import urllib.request

__version__ = "0.1.0"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "munimtechnologies/px4-log-health"
GPS_REPO = "munimtechnologies/px4-gps-lag"
API_MAIN = "https://api.github.com/repos/%s/commits/main"
RAW = "https://raw.githubusercontent.com/%s/%s/%s"
CHECKUP_TAG = "v0.3.9"
TARBALL = "https://codeload.github.com/%s/tar.gz/refs/tags/%s" % (REPO, CHECKUP_TAG)
PUBLISHED = {"studies/compare-2026/PLAN.md": "PLAN.md", "studies/compare-2026/pairs.py": "pairs.py",
             "compare.py": "compare.py", "test_compare.py": "test_compare.py"}
UPGRADE_FILES = ("studies/upgrade-2026/v1/pairs.jsonl", "studies/upgrade-2026/ext/pairs-ext.jsonl")
DBINFO = "https://review.px4.io/dbinfo"
DOWNLOAD = "https://review.px4.io/download?log=%s"
UA = ("px4-log-health-compare/%s (MV2 of Munim, Inc.; mv2@munimtech.com; research on public logs)"
      % __version__)
WAIT_MIN = 90
MIN_DURATION_S = 180.0
MIN_SERIES = (1, 13)
MAX_GAP_DAYS = 7
MAX_BYTES_NULL = 50 * 1000 * 1000
MAX_BYTES_UPGRADE = 80 * 1000 * 1000
TARGET_NULL = 40
MAX_DOWNLOADS = 220
BUDGET_MIN = 200
DELAY_S = 6.0
NOT_MULTICOPTER = ("vtol", "helicopter", "fixed", "wing", "rover", "boat", "submarine", "airship")
FIRE = ("worse", "better", "changed")
ITEMS = ("gps_lag_ms", "gps_delay_status", "gps_innov_excess_ms", "vibration_median", "clipping_per_min",
         "motor_saturation_share", "compass_coupling", "battery_r_collapse", "battery_warning",
         "failsafe_in_flight", "satellites_median", "eph_median")


class Blocked(Exception):
    pass


# ---------------------------------------------------------------- selection (pure, tested in self_test)

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


def is_multicopter(mav_type):
    t = str(mav_type or "").lower()
    if not t or any(w in t for w in NOT_MULTICOPTER):
        return False
    return "rotor" in t or "copter" in t


def vehicle_hash(uuid):
    return hashlib.sha256(str(uuid).encode()).hexdigest()[:12] if uuid else None


def placeholder_uuid(uuid):
    s = str(uuid or "").lower()
    return len(s) < 8 or set(s) <= set("0f-")


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
    if duration < MIN_DURATION_S or not is_multicopter(entry.get("mav_type")):
        return None
    rs = release_series(entry.get("ver_sw_release"))
    if rs is None or rs[0] < MIN_SERIES:
        return None
    return {"log_id": str(entry["log_id"]), "v": vehicle_hash(entry.get("vehicle_uuid")),
            "date": str(entry.get("log_date") or ""), "ver_sw": str(entry.get("ver_sw") or ""),
            "rel": str(entry.get("ver_sw_release") or ""), "hw": str(entry.get("sys_hw") or ""),
            "af": str(entry.get("sys_autostart_id") or ""), "mav": str(entry.get("mav_type") or ""),
            "dur": int(duration)}


def day_number(date):
    """Days since 1970-01-01 of a 'YYYY-MM-DD' date, or None."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date or "")
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    # days_from_civil (Howard Hinnant)
    y -= mo <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (mo + (-3 if mo > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def same_build(a, b):
    if a["rel"] != b["rel"] or a["hw"] != b["hw"] or a["af"] != b["af"]:
        return False
    return a["ver_sw"] == b["ver_sw"] if (a["ver_sw"] and b["ver_sw"]) else True


def pick_pair(logs):
    """The most recent two adjacent logs (upload date, then log id) of one vehicle with the same build,
    at most MAX_GAP_DAYS apart, or None."""
    logs = sorted(logs, key=lambda e: (e["date"], e["log_id"]))
    for k in range(len(logs) - 1, 0, -1):
        a, b = logs[k - 1], logs[k]
        da, db = day_number(a["date"]), day_number(b["date"])
        if da is None or db is None or db - da > MAX_GAP_DAYS:
            continue
        if same_build(a, b):
            return a, b
    return None


def select_pairs(entries):
    """[(vehicle hash, before, after)] in order of vehicle hash."""
    by_v = {}
    for e in entries:
        by_v.setdefault(e["v"], []).append(e)
    out = []
    for v in sorted(by_v):
        p = pick_pair(by_v[v])
        if p:
            out.append((v, p[0], p[1]))
    return out


def quantile(xs, p):
    """Linear-interpolated quantile of a list (like numpy's default), or None if empty."""
    xs = sorted(xs)
    if not xs:
        return None
    pos = (len(xs) - 1) * p
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def item_of(row, name):
    return next((i for i in ((row.get("compare") or {}).get("items") or []) if i.get("item") == name), None)


def item_stats(rows):
    out = {}
    for name in ITEMS:
        its = [item_of(r, name) for r in rows]
        comp = [i for i in its if i and i["status"] != "not comparable"]
        fired = [i for i in comp if i["status"] in FIRE]
        out[name] = {"comparable": len(comp), "fired": len(fired),
                     "worse": sum(1 for i in comp if i["status"] == "worse"),
                     "better": sum(1 for i in comp if i["status"] == "better"),
                     "share_fired": round(len(fired) / len(comp), 4) if comp else None}
    return out


def spread(rows):
    """Distributions of the size of each change across pairs."""
    def dist(xs):
        xs = [x for x in xs if x is not None and math.isfinite(x)]
        return {"n": len(xs), "median": _r(quantile(xs, 0.5)), "p90": _r(quantile(xs, 0.9)),
                "p95": _r(quantile(xs, 0.95)), "max": _r(max(xs) if xs else None)}
    lag, inn, vib, sat, comp = [], [], [], [], []
    for r in rows:
        i = item_of(r, "gps_lag_ms")
        if i and i["status"] != "not comparable" and i["delta"] is not None:
            lag.append(abs(i["delta"]))
        i = item_of(r, "gps_innov_excess_ms")
        if i and i["status"] != "not comparable" and i["delta"] is not None:
            inn.append(abs(i["delta"]))
        i = item_of(r, "vibration_median")
        if i and i["status"] != "not comparable" and (i["before"] or 0) > 0 and (i["after"] or 0) > 0:
            vib.append(abs(math.log2(i["after"] / i["before"])))
        i = item_of(r, "motor_saturation_share")
        if i and i["status"] != "not comparable" and i["delta"] is not None:
            sat.append(abs(i["delta"]))
        i = item_of(r, "compass_coupling")
        if i and i["status"] != "not comparable" and i["delta"] is not None:
            comp.append(abs(i["delta"]))
    return {"gps_lag_abs_ms": dist(lag), "innov_excess_abs_change_ms": dist(inn),
            "vibration_abs_log2_ratio": dist(vib), "saturation_abs_points": dist(sat),
            "compass_coupling_abs_pct": dist(comp)}


def _r(x, nd=4):
    return None if x is None else round(x, nd)


def predicate(share, n, limit, need, low=None):
    if share is None or n < need:
        return {"result": "too few", "n": n, "need": need, "share": share}
    ok = share <= limit + 1e-12 and (low is None or share >= low - 1e-12)
    return {"result": "held" if ok else "failed", "n": n, "share": share, "limit": limit, "low": low}


def grade(null_rows, config_rows):
    """The pre-registered checks of PLAN.md, computed (the GRADE adds the reading)."""
    st = item_stats(null_rows)
    sp = spread(null_rows)
    n = len(null_rows)
    g = {"G1_gate": {"result": "passed" if n >= 30 else "failed", "n_null": n, "need": 30}}
    lag = st["gps_lag_ms"]
    p1 = predicate(lag["share_fired"], lag["comparable"], 0.10, 12)
    med = sp["gps_lag_abs_ms"]["median"]
    if p1["result"] != "too few":
        p1["median_abs_ms"] = med
        if med is None or med > 10.0:
            p1["result"] = "failed"
    g["P1_gps_lag"] = p1
    g["P2_vibration"] = predicate(st["vibration_median"]["share_fired"], st["vibration_median"]["comparable"], 0.15, 12)
    g["P3_saturation"] = predicate(st["motor_saturation_share"]["share_fired"],
                                   st["motor_saturation_share"]["comparable"], 0.10, 12)
    g["P4_compass"] = predicate(st["compass_coupling"]["share_fired"], st["compass_coupling"]["comparable"], 0.25, 10)
    g["P5_battery"] = predicate(st["battery_r_collapse"]["share_fired"], st["battery_r_collapse"]["comparable"],
                                0.10, 10)
    worse = sum(1 for r in null_rows if (r.get("compare") or {}).get("worse"))
    g["P6_any_worse"] = predicate(round(worse / n, 4) if n else None, n, 0.30, 30)
    g["P6_any_worse"]["pairs_with_a_worse_item"] = worse
    m = n + len(config_rows)
    g["P7_settings_changed"] = predicate(round(len(config_rows) / m, 4) if m else None, m, 0.60, 30, low=0.20)
    return g


def upgrade_check(rows):
    """U1: our lag change against the published upgrade-2026 lags (post - pre), within 5 ms."""
    out = []
    for r in rows:
        i = item_of(r, "gps_lag_ms")
        pub = r.get("published") or {}
        mine = i["delta"] if i and i["status"] != "not comparable" else None
        pd = (None if pub.get("pre_lag_ms") is None or pub.get("post_lag_ms") is None
              else round(pub["post_lag_ms"] - pub["pre_lag_ms"], 1))
        out.append({"vehicle": r["vehicle"], "ours_ms": mine, "published_ms": pd,
                    "within_5ms": None if mine is None or pd is None else abs(mine - pd) <= 5.0,
                    "gps_lag_status": i["status"] if i else None})
    both = [x for x in out if x["within_5ms"] is not None]
    return {"pairs": out, "both_measured": len(both), "within_5ms": sum(1 for x in both if x["within_5ms"]),
            "result": "held" if len(both) >= 10 and sum(1 for x in both if x["within_5ms"]) >= 10
            else ("too few" if len(both) < 10 else "failed")}


def self_test():
    assert release_series("v1.16.0 255") == ((1, 16), "release")
    assert release_series("v1.17.0-rc2 0") is not None
    assert release_series("v1.12.3 0") == ((1, 12), "dev")
    assert is_multicopter("Quadrotor") and not is_multicopter("VTOL Quadrotor") and not is_multicopter("Fixed Wing")
    assert placeholder_uuid("000000000000") and placeholder_uuid("") and not placeholder_uuid("0012ab45cd")
    assert day_number("1970-01-01") == 0 and day_number("2026-11-11") - day_number("2026-11-04") == 7
    assert day_number("2024-03-01") - day_number("2024-02-28") == 2 and day_number("x") is None
    base = {"rel": "v1.16.0 255", "hw": "PX4_FMU_V6C", "af": "4001", "ver_sw": "abc", "v": "h"}
    logs = [dict(base, log_id="a", date="2026-01-01"), dict(base, log_id="b", date="2026-01-03"),
            dict(base, log_id="c", date="2026-03-01"), dict(base, log_id="d", date="2026-03-20")]
    assert pick_pair(logs) == (logs[0], logs[1])          # c-d 19 days apart, b-c 57 days
    logs.append(dict(base, log_id="e", date="2026-03-21", ver_sw="other"))
    assert pick_pair(logs) == (logs[0], logs[1])          # d-e differ in build
    logs.append(dict(base, log_id="f", date="2026-03-22", ver_sw="other"))
    assert pick_pair(logs) == (logs[4], logs[5])
    assert quantile([1, 2, 3, 4], 0.5) == 2.5 and quantile([5], 0.95) == 5 and quantile([], 0.5) is None
    assert abs(quantile(list(range(1, 21)), 0.95) - 19.05) < 1e-9
    assert predicate(0.1, 20, 0.10, 12)["result"] == "held"
    assert predicate(0.15, 20, 0.10, 12)["result"] == "failed"
    assert predicate(0.0, 5, 0.10, 12)["result"] == "too few"
    assert predicate(0.1, 40, 0.60, 30, low=0.20)["result"] == "failed"
    e = {"log_id": "x", "vehicle_uuid": "0123456789abcdef", "duration_s": 300, "mav_type": "Quadrotor",
         "ver_sw_release": "v1.15.4 255", "sys_hw": "PX4_FMU_V5", "sys_autostart_id": 4001, "log_date": "2026-01-02",
         "ver_sw": "deadbeef", "source": "webui", "type": "personal"}
    c = compact(e)
    assert c and c["v"] == vehicle_hash("0123456789abcdef") and c["af"] == "4001" and c["dur"] == 300
    assert compact(dict(e, sys_hw="SITL")) is None and compact(dict(e, duration_s=100)) is None
    assert compact(dict(e, ver_sw_release="v1.12.3 255")) is None
    assert compact(dict(e, mav_type="Fixed Wing")) is None


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


def install_checkup(res):
    data = get(TARBALL)
    res["checkup_tarball_sha256"] = sha(data)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
        t.extractall("/tmp/plh", filter="data")
    tops = [d for d in os.listdir("/tmp/plh") if os.path.isfile(os.path.join("/tmp/plh", d, "checkup.py"))]
    assert len(tops) == 1, os.listdir("/tmp/plh")
    repo = os.path.join("/tmp/plh", tops[0])
    p = subprocess.run([sys.executable, "fetch_deps.py"], cwd=repo, capture_output=True, text=True)
    res["fetch_deps_exit"] = p.returncode
    assert p.returncode == 0, p.stdout[-1000:] + p.stderr[-1000:]
    for name in ("gps_lag.py", "innov.py", "compass.py", "battery_r.py", "health.py", "baseline.json"):
        assert os.path.isfile(os.path.join(repo, name)), name
    sys.path.insert(0, repo)
    import checkup
    assert checkup.__version__ == CHECKUP_TAG[1:], checkup.__version__
    with open(os.path.join(repo, "fetched.json"), encoding="utf-8") as f:
        res["fetched"] = json.load(f)
    return repo


def download(session, log_id, path, max_bytes):
    """'ok', 'missing', 'too large', 'not a ULog' or 'failed'. Raises Blocked on 403/444."""
    import requests
    url = DOWNLOAD % log_id
    for attempt in range(5):
        try:
            with session.get(url, stream=True, timeout=600) as r:
                if r.status_code in (403, 444):
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
                n = 0
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


# ---------------------------------------------------------------- measuring

LOG_KEYS = {"health": ("health_ok", "health_reason", "flight_s", "release", "vib_median", "vib_band", "clip_count",
                       "clip_per_min", "sat_share", "sats_median", "eph_median", "battery_warning_max",
                       "failsafe_in_flight"),
            "gps_verdict": ("status", "lag_ms", "se_ms", "param_ms", "d_ms", "innov"),
            "compass": ("level", "class", "coupling_pct"),
            "battery": ("level", "collapsed", "r_end_mohm", "bat_r_internal")}


def summary_of(row):
    if row is None:
        return None
    out = {k: {f: (row.get(k) or {}).get(f) for f in fields} for k, fields in LOG_KEYS.items()}
    inn = (row.get("gps") or {}).get("innov") or {}
    out["innov"] = {"passed": inn.get("passed"), "implied_excess_ms": inn.get("implied_excess_ms"),
                    "implied_se_ms": inn.get("implied_se_ms")}
    out["gps_dynmodel"] = (row.get("gps_dynmodel") or {}).get("value")
    return out


class Measurer:
    def __init__(self, session, folder, baseline):
        self.session, self.folder, self.baseline = session, folder, baseline
        self.downloads = 0
        os.makedirs(folder, exist_ok=True)

    def one(self, log_id, max_bytes):
        import checkup
        import compare
        if self.downloads:
            time.sleep(DELAY_S)
        self.downloads += 1
        path = os.path.join(self.folder, log_id + ".ulg")
        status = download(self.session, log_id, path, max_bytes)
        info = {"log_id": log_id, "download": status}
        if status != "ok":
            return info, None, None
        row = params = None
        try:
            with open(path, "rb") as f:
                data = f.read()
            info["bytes"], info["sha256"] = len(data), sha(data)
            del data
            row = compare.plain_row(checkup.check(path, self.baseline))
            params = compare.read_params(path)
        except Exception as exc:
            info["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        finally:
            if os.path.exists(path):
                os.remove(path)
            gc.collect()
        return info, row, params

    def pair(self, a_id, b_id, max_bytes):
        import compare
        ia, ra, pa = self.one(a_id, max_bytes)
        if ra is None:
            return {"before": ia, "after": None, "compare": None, "state": "before not read"}
        ib, rb, pb = self.one(b_id, max_bytes)
        if rb is None:
            return {"before": ia, "after": ib, "compare": None, "state": "after not read"}
        res = compare.compare_rows(ra, rb, pa, pb)
        both = bool((ra.get("health") or {}).get("health_ok") and (rb.get("health") or {}).get("health_ok"))
        return {"before": dict(ia, figures=summary_of(ra)), "after": dict(ib, figures=summary_of(rb)),
                "compare": res, "both_health_ok": both, "params_read": res["params"] is not None,
                "state": "measured" if both and res["params"] is not None else "not both read"}


def upgrade_pairs():
    out = []
    for path in UPGRADE_FILES:
        for line in get(RAW % (GPS_REPO, "main", path), 120).decode("utf-8").splitlines():
            if line.strip():
                p = json.loads(line)
                out.append({"vehicle": p["vehicle"], "before": p["pre"]["log_id"], "after": p["post"]["log_id"],
                            "published": {"pre_lag_ms": p["pre"].get("lag_ms"), "post_lag_ms": p["post"].get("lag_ms"),
                                          "pre_release": p["pre"].get("ver_sw_release"),
                                          "post_release": p["post"].get("ver_sw_release"),
                                          "pre_dynmodel": p["pre"].get("dynmodel"),
                                          "post_dynmodel": p["post"].get("dynmodel"), "source": path}})
    return out


def stream_dbinfo(session, res):
    import ijson
    kept, seen = [], 0
    with session.get(DBINFO, stream=True, timeout=900) as resp:
        resp.raise_for_status()
        resp.raw.decode_content = True
        for entry in ijson.items(resp.raw, "item"):
            seen += 1
            c = compact(entry)
            if c:
                kept.append(c)
    res["dbinfo"] = {"entries": seen, "eligible": len(kept)}
    return kept


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def main(out):
    os.makedirs(out, exist_ok=True)
    res = {"pairs": __version__, "ok": False, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    rows = []
    try:
        self_test()
        p = subprocess.run([sys.executable, "-m", "unittest", "-v", "test_compare"], cwd=HERE,
                           capture_output=True, text=True)
        with open(os.path.join(out, "tests.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(p.stdout + p.stderr)
        res["tests_exit"] = p.returncode
        assert p.returncode == 0, "test_compare failed: %s" % (p.stdout + p.stderr)[-1500:]
        wait_for_plan(res)
        install_checkup(res)
        import compare
        import health
        import requests
        res["compare"] = compare.__version__
        session = requests.Session()
        session.headers["User-Agent"] = UA
        m = Measurer(session, "/tmp/logs", health.load_baseline(health.BASELINE))

        # upgrade pairs
        ups = upgrade_pairs()
        res["upgrade_pairs_listed"] = len(ups)
        for u in ups:
            r = m.pair(u["before"], u["after"], MAX_BYTES_UPGRADE)
            r.update(group="upgrade", vehicle=u["vehicle"], published=u["published"])
            rows.append(r)
            write_jsonl(os.path.join(out, "pairs-rows.jsonl"), rows)

        # null pairs
        entries = stream_dbinfo(session, res)
        cands = select_pairs(entries)
        del entries
        gc.collect()
        res["candidate_vehicles"] = len(cands)
        upgrade_vehicles = {u["vehicle"] for u in ups}
        t0, n_null, stop = time.time(), 0, "list exhausted"
        for v, a, b in cands:
            if n_null >= TARGET_NULL:
                stop = "target reached"
                break
            if m.downloads >= MAX_DOWNLOADS:
                stop = "download cap"
                break
            if time.time() - t0 > BUDGET_MIN * 60:
                stop = "time budget"
                break
            r = m.pair(a["log_id"], b["log_id"], MAX_BYTES_NULL)
            r.update(vehicle=v, listing={"before_date": a["date"], "after_date": b["date"], "release": a["rel"],
                                         "ver_sw": a["ver_sw"][:12], "hw": a["hw"], "airframe": a["af"],
                                         "mav_type": a["mav"], "also_in_upgrade": v in upgrade_vehicles})
            if r["state"] != "measured":
                r["group"] = "unmeasured"
            elif r["compare"]["params"]["settings"] or r["compare"]["firmware"]["changed"]:
                r["group"] = "config"
            else:
                r["group"] = "null"
                n_null += 1
            rows.append(r)
            write_jsonl(os.path.join(out, "pairs-rows.jsonl"), rows)
        res["stop"] = stop
        res["downloads"] = m.downloads
        res["ok"] = True
    except Exception:
        res["error"] = traceback.format_exc()[-3000:]
        print(res["error"], flush=True)
    write_jsonl(os.path.join(out, "pairs-rows.jsonl"), rows)
    null = [r for r in rows if r.get("group") == "null"]
    config = [r for r in rows if r.get("group") == "config"]
    upgrade = [r for r in rows if r.get("group") == "upgrade" and r.get("compare")]
    summary = {"pairs": __version__, "counts": {g: sum(1 for r in rows if r.get("group") == g)
                                               for g in ("upgrade", "null", "config", "unmeasured")},
               "null_items": item_stats(null), "null_spread": spread(null),
               "config_items": item_stats(config), "config_spread": spread(config),
               "upgrade_items": item_stats(upgrade), "upgrade_check": upgrade_check(upgrade),
               "null_verdicts": {}, "config_settings_changed_median": None}
    for r in null:
        v = r["compare"]["verdict"]
        summary["null_verdicts"][v] = summary["null_verdicts"].get(v, 0) + 1
    if config:
        summary["config_settings_changed_median"] = quantile([len(r["compare"]["params"]["settings"]) for r in config], 0.5)
    try:
        summary["grade"] = grade(null, config)
    except Exception:
        summary["grade_error"] = traceback.format_exc()[-2000:]
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, indent=1)
        f.write("\n")
    with open(os.path.join(out, "job-fetched.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=1)
        f.write("\n")
    print(json.dumps({"res": res, "counts": summary["counts"], "grade": summary.get("grade")}, indent=1)[:6000],
          flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/out"))

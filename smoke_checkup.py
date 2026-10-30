"""smoke_checkup.py: checkup.py and the GitHub Action's script on three real logs.

Picks, from fleet-health-2026's logs.jsonl, logs of at most 15 MB that were
read: the first whose GPS lag passed and is more than 50 ms off
EKF2_GPS_DELAY with innovations that passed in px4-gps-lag's confirm run,
the first whose lag passed within 20 ms of the setting, and the first from
PX4 v1.12 or older. Downloads each (10 s apart, stop on 403/444) and runs
checkup.py on all three. Every health figure, the lag and the implied
excess must match the recorded ones. Then runs action.sh from a folder with
only checkup.py, health.py and baseline.json, so it must fetch gps_lag.py
and innov.py from the pinned commit itself, and its rows must match too.

    python smoke_checkup.py health-logs.jsonl confirm-innov.jsonl /out

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

import json
import os
import shutil
import subprocess
import sys
import time

import checkup
import fleet
import health

SAME = ("flight_s", "vib_median", "vib_p95", "clip_count", "jam_median", "jam_state_max",
        "spoof_state_max", "battery_warning_max", "failsafe_in_flight", "sat_share")
TMP = "/tmp/checkup-smoke"


def pick(rows, innov):
    ok = [r for r in rows if r.get("health_ok") and (r.get("size_mb") or 99) <= 15]

    def lag(r):
        return r.get("lag") or {}

    def d(r):
        return lag(r).get("lag_minus_param_ms")
    off = [r for r in ok if lag(r).get("passed") and d(r) is not None and abs(d(r)) > 50
           and (innov.get(r["log_id"]) or {}).get("passed")]
    near = [r for r in ok if lag(r).get("passed") and d(r) is not None and abs(d(r)) <= 20]
    old = [r for r in ok if health.vib_units(lag(r).get("ver_sw_release")) == "m/s"]
    chosen = []
    for group in (off, near, old):
        for r in group:
            if r not in chosen:
                chosen.append(r)
                break
    return chosen


def compare(row, inn, rep):
    """Differences between the recorded figures and checkup's report."""
    h, g = rep["health"], rep["gps"]
    diffs = {k: (row.get(k), h.get(k)) for k in SAME if row.get(k) != h.get(k)}
    lag = row.get("lag") or {}
    new_lag = g.get("lag") or {}
    if bool(lag.get("passed")) != bool(new_lag.get("passed")):
        diffs["lag_passed"] = (lag.get("passed"), new_lag.get("passed"))
    elif lag.get("passed") and abs(lag["lag_ms"] - new_lag["lag_ms"]) > 0.11:
        diffs["lag_ms"] = (lag["lag_ms"], new_lag["lag_ms"])
    if inn and inn.get("passed"):
        got = (g.get("innov") or {}).get("implied_excess_ms")
        if got is None or abs(got - inn["implied_excess_ms"]) > 0.11:
            diffs["implied_excess_ms"] = (inn["implied_excess_ms"], got)
    return diffs


def main(rows_path, innov_path, out_dir):
    with open(rows_path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    with open(innov_path, encoding="utf-8") as f:
        innov = {r["log_id"]: r for r in (json.loads(line) for line in f if line.strip())}
    chosen = pick(rows, innov)
    print("picked %s" % [r["log_id"] for r in chosen], flush=True)
    import requests
    session = requests.Session()
    session.headers["User-Agent"] = fleet.USER_AGENT.replace("px4-gps-lag/%s" % fleet.__version__,
                                                             "px4-log-health/%s" % checkup.__version__)
    os.makedirs(TMP, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    problems, paths = [], []
    for i, row in enumerate(chosen):
        if i:
            time.sleep(10)
        path = os.path.join(TMP, "%s.ulg" % row["log_id"])
        got = fleet.download(session, row["log_id"], path)
        if got != "ok":
            problems.append("%s: download %s" % (row["log_id"], got))
            continue
        paths.append((row, path))

    md, js = os.path.join(out_dir, "sample-report.md"), os.path.join(out_dir, "sample-report.jsonl")
    checkup.main([p for _, p in paths] + ["--md", md, "--json", js])
    with open(js, encoding="utf-8") as f:
        reports = [json.loads(line) for line in f]
    for (row, _), rep in zip(paths, reports):
        diffs = compare(row, innov.get(row["log_id"]), rep)
        line = {"log_id": row["log_id"], "release": rep["health"].get("release"),
                "gps_verdict": rep["gps_verdict"], "flags": rep["health"].get("flags"), "same": not diffs}
        print(json.dumps(line), flush=True)
        if diffs:
            problems.append("%s: %s" % (row["log_id"], diffs))

    act = os.path.join(TMP, "action")
    os.makedirs(act, exist_ok=True)
    for name in ("checkup.py", "health.py", "baseline.json", "action.sh"):
        shutil.copy(name, act)
    env = dict(os.environ, GITHUB_ACTION_PATH=act, GITHUB_STEP_SUMMARY=os.path.join(out_dir, "action-summary.md"),
               INPUT_LOGS="*.ulg", INPUT_JSON=os.path.join(out_dir, "action.jsonl"), INPUT_FAIL_ON_FLAG="false")
    run = subprocess.run(["bash", os.path.join(act, "action.sh")], cwd=TMP, env=env)
    if run.returncode != 0:
        problems.append("action.sh exited %d" % run.returncode)
    else:
        with open(env["INPUT_JSON"], encoding="utf-8") as f:
            acted = {os.path.basename(r["log"]): r for r in (json.loads(line) for line in f)}
        if not os.path.exists(os.path.join(act, "innov.py")):
            problems.append("action.sh did not fetch innov.py")
        for row, path in paths:
            rep = acted.get(os.path.basename(path))
            if rep is None:
                problems.append("%s: not in action.jsonl" % row["log_id"])
                continue
            diffs = compare(row, innov.get(row["log_id"]), rep)
            if diffs:
                problems.append("%s (action): %s" % (row["log_id"], diffs))
    for _, path in paths:
        os.remove(path)
    print("\n".join(problems) or "all figures match", flush=True)
    return 1 if problems or len(paths) < 3 else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))

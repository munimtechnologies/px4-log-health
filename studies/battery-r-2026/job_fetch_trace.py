"""For the px4-log-health battery-r-2026 trace step (published with PLAN-trace.md). Downloads
replay.py 0.1.0 from px4-log-health's main and checks its git blob hash (the exact file the replay
and simulation steps use). Records sha256 hashes (line endings normalised) in
job-fetched-trace.json. Saves, with line numbers, the lines of PX4's src/lib/battery/battery.cpp and
battery.h (main, today) that battr_trace.py's recovery of the raw estimate rests on (the prediction,
the rejected-update branch, the logged fields), as px4-trace-lines.txt.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import hashlib
import json
import sys
import time
import urllib.request

BASE = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/"
REPLAY = BASE + "replay.py"
REPLAY_BLOB = "4199dd45a5e7cae90ed81fc157cf2cc297511936"
PX4 = "https://raw.githubusercontent.com/PX4/PX4-Autopilot/main/src/lib/battery/"
PX4_COMMIT = "https://api.github.com/repos/PX4/PX4-Autopilot/commits?path=src/lib/battery/battery.cpp&per_page=1"
KEYWORDS = ("_voltage_prediction", "_prediction_error", "voltage_prediction", "prediction_error",
            "estimation_covariance_norm", "_RLS_est(0)", "_RLS_est(1)", "internal_resistance_estimate",
            "LAMBDA", "R_DEFAULT", "OCV_COVARIANCE", "R_COVARIANCE", "FLT_EPSILON", "_battery_initialized",
            "_last_unconnected_timestamp", "LITHIUM_BATTERY_RECOGNITION_VOLTAGE")
FUNCTIONS = ("void Battery::updateInternalResistanceEstimation", "void Battery::resetInternalResistanceEstimation",
             "void Battery::updateBatteryStatus", "float Battery::calculateStateOfChargeVoltageBased")


def get(url):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "User-Agent": "px4-log-health-trace (MV2 of Munim, Inc.)"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def sha(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def git_blob(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


fetched = {}
rep = None
for attempt in range(5):
    try:
        rep = get(REPLAY)
        break
    except Exception as exc:
        print("replay.py not readable yet: %s" % str(exc)[:120], flush=True)
        time.sleep(60)
if rep is None:
    sys.exit("could not download replay.py")
if git_blob(rep) != REPLAY_BLOB:
    sys.exit("replay.py on px4-log-health's main is not blob %s (got %s)" % (REPLAY_BLOB, git_blob(rep)))
with open("replay.py", "wb") as f:
    f.write(rep)
fetched["px4-log-health/main/studies/battery-r-2026/replay.py"] = {"sha256": sha(rep), "bytes": len(rep),
                                                                    "git_blob": REPLAY_BLOB}
for local in ("PLAN-trace.md", "battr_trace.py", "test_battr_trace.py", "job_fetch_trace.py", "smoke_trace.py"):
    try:
        data = open(local, "rb").read()
        fetched["local/" + local] = {"sha256": sha(data), "bytes": len(data), "git_blob": git_blob(data)}
    except OSError:
        fetched["local/" + local] = None

out = []
try:
    commit = None
    try:
        commit = json.loads(get(PX4_COMMIT).decode("utf-8"))[0]["sha"]
    except Exception as exc:
        print("could not read the commit: %s" % str(exc)[:120], flush=True)
    out.append("PX4-Autopilot main, src/lib/battery/, last commit touching battery.cpp: %s" % commit)
    out.append("fetched %s UTC; whole functions: %s; other lines containing: %s" % (
        time.strftime("%Y-%m-%d %H:%M", time.gmtime()), ", ".join(FUNCTIONS), ", ".join(KEYWORDS)))
    for name in ("battery.h", "battery.cpp"):
        src = get(PX4 + name)
        fetched["PX4/PX4-Autopilot/main/src/lib/battery/" + name] = {"sha256": sha(src), "bytes": len(src),
                                                                      "commit": commit}
        out.append("")
        out.append("== %s (sha256 %s)" % (name, sha(src)))
        in_fn = False
        for n, line in enumerate(src.decode("utf-8", "replace").splitlines(), 1):
            if any(fn in line for fn in FUNCTIONS):
                in_fn = True
            if in_fn or any(k in line for k in KEYWORDS):
                out.append("%5d: %s" % (n, line.rstrip()))
            if in_fn and line.startswith("}"):
                in_fn = False
                out.append("")
    with open("px4-trace-lines.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
except Exception as exc:
    print("could not save PX4's lines: %s" % str(exc)[:200], flush=True)

with open("job-fetched-trace.json", "w") as f:
    json.dump(fetched, f, indent=1)
print(json.dumps(fetched, indent=1), flush=True)

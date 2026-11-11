"""For the px4-log-health battery-r-2026 simulation step (published with PLAN-sim.md). Waits until
px4-log-health's main has studies/battery-r-2026/PLAN-sim.md, then downloads replay.py 0.1.0 from
main and checks its git blob hash (the exact file the replay step was graded with). Records sha256
hashes (line endings normalised) in job-fetched-sim.json. Also saves the lines of PX4's
src/lib/battery/battery.cpp and battery.h (main, today) that the estimator port relies on, with
line numbers, as px4-estimator-lines.txt.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import hashlib
import json
import sys
import time
import urllib.request

BASE = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/"
PLAN = BASE + "PLAN-sim.md"
REPLAY = BASE + "replay.py"
REPLAY_BLOB = "4199dd45a5e7cae90ed81fc157cf2cc297511936"
PX4 = "https://raw.githubusercontent.com/PX4/PX4-Autopilot/main/src/lib/battery/"
PX4_COMMIT = "https://api.github.com/repos/PX4/PX4-Autopilot/commits?path=src/lib/battery/battery.cpp&per_page=1"
KEYWORDS = ("InternalResistanceEstimation", "LAMBDA", "OCV_DEFAULT", "R_DEFAULT", "OCV_COVARIANCE",
            "R_COVARIANCE", "_estimation_covariance", "_internal_resistance_estimate", "_battery_initialized",
            "_last_unconnected_timestamp", "FLT_EPSILON", "_internal_resistance_initialized",
            "LITHIUM_BATTERY_RECOGNITION_VOLTAGE", "_ocv_estimate", "_params.r_internal")
WAIT_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0


def get(url):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "User-Agent": "px4-log-health-sim (MV2 of Munim, Inc.)"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def sha(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def git_blob(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


t0 = time.time()
while True:
    try:
        plan = get(PLAN)
        if b"battery-r-2026, simulation" in plan:
            break
        print("PLAN-sim.md on main does not look right yet", flush=True)
    except Exception as exc:
        print("not on main yet: %s" % str(exc)[:120], flush=True)
    if time.time() - t0 > WAIT_MIN * 60:
        sys.exit("PLAN-sim.md did not appear on px4-log-health's main")
    time.sleep(300)

fetched = {"px4-log-health/main/studies/battery-r-2026/PLAN-sim.md": {"sha256": sha(plan), "bytes": len(plan)}}
rep = get(REPLAY)
if git_blob(rep) != REPLAY_BLOB:
    sys.exit("replay.py on px4-log-health's main is not blob %s (got %s)" % (REPLAY_BLOB, git_blob(rep)))
with open("replay.py", "wb") as f:
    f.write(rep)
fetched["px4-log-health/main/studies/battery-r-2026/replay.py"] = {"sha256": sha(rep), "bytes": len(rep),
                                                                    "git_blob": REPLAY_BLOB}
for local in ("PLAN-sim.md", "sim.py", "test_sim.py", "job_fetch_sim.py"):
    data = open(local, "rb").read()
    fetched["local/" + local] = {"sha256": sha(data), "bytes": len(data)}

out = []
try:
    commit = None
    try:
        commit = json.loads(get(PX4_COMMIT).decode("utf-8"))[0]["sha"]
    except Exception as exc:
        print("could not read the commit: %s" % str(exc)[:120], flush=True)
    out.append("PX4-Autopilot main, src/lib/battery/, last commit touching battery.cpp: %s" % commit)
    out.append("fetched %s UTC; lines containing: %s" % (time.strftime("%Y-%m-%d %H:%M", time.gmtime()),
                                                         ", ".join(KEYWORDS)))
    for name in ("battery.h", "battery.cpp"):
        src = get(PX4 + name)
        fetched["PX4/PX4-Autopilot/main/src/lib/battery/" + name] = {"sha256": sha(src), "bytes": len(src),
                                                                      "commit": commit}
        out.append("")
        out.append("== %s (sha256 %s)" % (name, sha(src)))
        lines = src.decode("utf-8", "replace").splitlines()
        in_fn = False
        for n, line in enumerate(lines, 1):
            if "void Battery::updateInternalResistanceEstimation" in line or \
                    "void Battery::resetInternalResistanceEstimation" in line or \
                    "void Battery::updateBatteryStatus" in line:
                in_fn = True
            if in_fn or any(k in line for k in KEYWORDS):
                out.append("%5d: %s" % (n, line.rstrip()))
            if in_fn and line.startswith("}"):
                in_fn = False
                out.append("")
    with open("px4-estimator-lines.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
except Exception as exc:
    print("could not save PX4's lines: %s" % str(exc)[:200], flush=True)

with open("job-fetched-sim.json", "w") as f:
    json.dump(fetched, f, indent=1)
print(json.dumps(fetched, indent=1), flush=True)

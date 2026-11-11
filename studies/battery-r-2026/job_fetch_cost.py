"""For the px4-log-health battery-r-2026 cost step (published with PLAN-cost.md). Waits until
px4-log-health's main has studies/battery-r-2026/PLAN-cost.md, then downloads the code the step
reuses (battr_logs.py 0.1.0 from px4-log-health, gnss_logs.py 0.1.0 from px4-gps-lag's
studies/gnss-2026) and the earlier rows (replay-rows.jsonl, and battr-, onset- and steps-rows.jsonl
for the logs' battery parameters). Records sha256 hashes (line endings normalised) in
job-fetched-cost.json. Also saves the lines of PX4's src/lib/battery/battery.cpp (main, today)
that the plan relies on, with line numbers, as px4-battery-lines.txt.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import hashlib
import json
import sys
import time
import urllib.request

BASE = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/"
PLAN = BASE + "PLAN-cost.md"
ROWS = (("replay-rows.jsonl", BASE + "replay-rows.jsonl"),
        ("battr-rows.jsonl", BASE + "battr-rows.jsonl"),
        ("onset-rows.jsonl", BASE + "onset-rows.jsonl"),
        ("steps-rows.jsonl", BASE + "steps-rows.jsonl"))
BATTR = BASE + "battr_logs.py"
GNSS = "https://raw.githubusercontent.com/munimtechnologies/px4-gps-lag/main/studies/gnss-2026/gnss_logs.py"
PX4_FILE = "https://raw.githubusercontent.com/PX4/PX4-Autopilot/main/src/lib/battery/battery.cpp"
PX4_COMMIT = "https://api.github.com/repos/PX4/PX4-Autopilot/commits?path=src/lib/battery/battery.cpp&per_page=1"
KEYWORDS = ("weight_v", "_state_of_charge", "_internal_resistance_estimate * current_a",
            "_params.r_internal * current_a", "_cell_voltage_filter_v", "math::interpolate",
            "determineWarning", "_discharged_mah", "_capacity_mah", "emergen_thr", "crit_thr",
            "low_thr", "void Battery::", "float Battery::", "uint8_t Battery::")
WAIT_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0


def get(url):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "User-Agent": "px4-log-health-cost (MV2 of Munim, Inc.)"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def sha(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


t0 = time.time()
while True:
    try:
        plan = get(PLAN)
        if b"battery-r-2026, cost" in plan:
            break
        print("PLAN-cost.md on main does not look right yet", flush=True)
    except Exception as exc:
        print("not on main yet: %s" % str(exc)[:120], flush=True)
    if time.time() - t0 > WAIT_MIN * 60:
        sys.exit("PLAN-cost.md did not appear on px4-log-health's main")
    time.sleep(300)

fetched = {"px4-log-health/main/studies/battery-r-2026/PLAN-cost.md": {"sha256": sha(plan), "bytes": len(plan)}}
battr = get(BATTR)
if b'__version__ = "0.1.0"' not in battr:
    sys.exit("battr_logs.py on px4-log-health's main is not 0.1.0")
gnss = get(GNSS)
if b'__version__ = "0.1.0"' not in gnss:
    sys.exit("gnss_logs.py on px4-gps-lag's main is not 0.1.0")
files = [("battr_logs.py", BATTR, battr), ("gnss_logs.py", GNSS, gnss)]
for local, url in ROWS:
    data = get(url)
    if data.count(b"\n") < 200:
        sys.exit("%s on main looks too short" % url)
    files.append((local, url, data))
for local, url, data in files:
    with open(local, "wb") as f:
        f.write(data)
    key = url.replace("https://raw.githubusercontent.com/munimtechnologies/", "")
    fetched[key] = {"sha256": sha(data), "bytes": len(data), "saved_as": local}
for local in ("PLAN-cost.md", "cost.py", "test_cost.py", "job_fetch_cost.py"):
    data = open(local, "rb").read()
    fetched["local/" + local] = {"sha256": sha(data), "bytes": len(data)}

try:
    src = get(PX4_FILE)
    commit = None
    try:
        commit = json.loads(get(PX4_COMMIT).decode("utf-8"))[0]["sha"]
    except Exception as exc:
        print("could not read the commit: %s" % str(exc)[:120], flush=True)
    lines = src.decode("utf-8", "replace").splitlines()
    out = ["PX4-Autopilot main, src/lib/battery/battery.cpp, last commit touching it: %s" % commit,
           "fetched %s UTC, sha256 %s; lines containing: %s" % (
               time.strftime("%Y-%m-%d %H:%M", time.gmtime()), sha(src), ", ".join(KEYWORDS)), ""]
    for n, line in enumerate(lines, 1):
        if any(k in line for k in KEYWORDS):
            out.append("%5d: %s" % (n, line.rstrip()))
    with open("px4-battery-lines.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    fetched["PX4/PX4-Autopilot/main/src/lib/battery/battery.cpp"] = {"sha256": sha(src), "bytes": len(src),
                                                                     "commit": commit}
except Exception as exc:
    print("could not save PX4's lines: %s" % str(exc)[:200], flush=True)

with open("job-fetched-cost.json", "w") as f:
    json.dump(fetched, f, indent=1)
print(json.dumps(fetched, indent=1), flush=True)

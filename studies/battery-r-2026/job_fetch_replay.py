"""For the px4-log-health battery-r-2026 replay (published with PLAN-replay.md). Waits until
px4-log-health's main has studies/battery-r-2026/PLAN-replay.md, then downloads the code the replay
reuses (battr_logs.py 0.1.0 from px4-log-health, gnss_logs.py 0.1.0 from px4-gps-lag's
studies/gnss-2026) and the three earlier passes' rows (saved as first-pass-rows.jsonl,
onset-rows.jsonl and steps-rows.jsonl). Records sha256 hashes (line endings normalised) in
job-fetched-replay.json. Exits non-zero if something does not appear within the wait or a version
is not the planned one.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import hashlib
import json
import sys
import time
import urllib.request

BASE = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/"
PLAN = BASE + "PLAN-replay.md"
ROWS = (("first-pass-rows.jsonl", BASE + "battr-rows.jsonl"),
        ("onset-rows.jsonl", BASE + "onset-rows.jsonl"),
        ("steps-rows.jsonl", BASE + "steps-rows.jsonl"))
BATTR = BASE + "battr_logs.py"
GNSS = "https://raw.githubusercontent.com/munimtechnologies/px4-gps-lag/main/studies/gnss-2026/gnss_logs.py"
WAIT_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0


def get(url):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def sha(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


t0 = time.time()
while True:
    try:
        plan = get(PLAN)
        if b"battery-r-2026, replay" in plan:
            break
        print("PLAN-replay.md on main does not look right yet", flush=True)
    except Exception as exc:
        print("not on main yet: %s" % str(exc)[:120], flush=True)
    if time.time() - t0 > WAIT_MIN * 60:
        sys.exit("PLAN-replay.md did not appear on px4-log-health's main")
    time.sleep(300)

fetched = {"px4-log-health/main/studies/battery-r-2026/PLAN-replay.md": {"sha256": sha(plan), "bytes": len(plan)}}
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
for local in ("PLAN-replay.md", "replay.py", "test_replay.py", "job_fetch_replay.py"):
    data = open(local, "rb").read()
    fetched["local/" + local] = {"sha256": sha(data), "bytes": len(data)}
with open("job-fetched-replay.json", "w") as f:
    json.dump(fetched, f, indent=1)
print(json.dumps(fetched, indent=1), flush=True)

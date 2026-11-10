"""For the px4-log-health battery-r-2026 onset job (published with PLAN-onset.md). Waits until
px4-log-health's main has studies/battery-r-2026/PLAN-onset.md and the first census's rows, then
downloads the code the pass reuses (battr_logs.py 0.1.0 from px4-log-health, gnss_logs.py 0.1.0 from
px4-gps-lag's studies/gnss-2026) and the first census's rows (saved as first-pass-rows.jsonl, whose
vehicles the pass skips). Records sha256 hashes (line endings normalised) in job-fetched-onset.json.
Exits non-zero if something does not appear within the wait or a version is not the planned one.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import hashlib
import json
import sys
import time
import urllib.request

BASE = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/"
PLAN = BASE + "PLAN-onset.md"
ROWS = BASE + "battr-rows.jsonl"
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
        rows = get(ROWS)
        if b"battery-r-2026 follow-up" in plan and rows.count(b"\n") >= 200:
            break
        print("PLAN-onset.md or battr-rows.jsonl on main does not look right yet", flush=True)
    except Exception as exc:
        print("not on main yet: %s" % str(exc)[:120], flush=True)
    if time.time() - t0 > WAIT_MIN * 60:
        sys.exit("PLAN-onset.md and battr-rows.jsonl did not both appear on px4-log-health's main")
    time.sleep(300)

battr = get(BATTR)
if b'__version__ = "0.1.0"' not in battr:
    sys.exit("battr_logs.py on px4-log-health's main is not 0.1.0")
gnss = get(GNSS)
if b'__version__ = "0.1.0"' not in gnss:
    sys.exit("gnss_logs.py on px4-gps-lag's main is not 0.1.0")
for name, data in (("battr_logs.py", battr), ("gnss_logs.py", gnss), ("first-pass-rows.jsonl", rows)):
    with open(name, "wb") as f:
        f.write(data)
fetched = {"px4-log-health/main/studies/battery-r-2026/battr_logs.py": {"sha256": sha(battr), "bytes": len(battr)},
           "px4-log-health/main/studies/battery-r-2026/battr-rows.jsonl": {"sha256": sha(rows), "bytes": len(rows)},
           "px4-log-health/main/studies/battery-r-2026/PLAN-onset.md": {"sha256": sha(plan), "bytes": len(plan)},
           "px4-gps-lag/main/studies/gnss-2026/gnss_logs.py": {"sha256": sha(gnss), "bytes": len(gnss)}}
for local in ("PLAN-onset.md", "onset.py", "test_onset.py", "job_fetch_onset.py"):
    data = open(local, "rb").read()
    fetched["local/" + local] = {"sha256": sha(data), "bytes": len(data)}
with open("job-fetched-onset.json", "w") as f:
    json.dump(fetched, f, indent=1)
print(json.dumps(fetched, indent=1), flush=True)

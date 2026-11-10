"""For the px4-log-health battery-r-2026 third pass (published with PLAN-steps.md). Waits until
px4-log-health's main has studies/battery-r-2026/PLAN-steps.md, the first census's rows and the
onset pass's rows, then downloads the code the pass reuses (battr_logs.py 0.1.0 and onset.py 0.1.0
from px4-log-health, gnss_logs.py 0.1.0 from px4-gps-lag's studies/gnss-2026) and both row files
(saved as first-pass-rows.jsonl and onset-rows.jsonl; their vehicles are skipped, and the first
census's rows are the held-out check). Records sha256 hashes (line endings normalised) in
job-fetched-steps.json. Exits non-zero if something does not appear within the wait or a version
is not the planned one.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import hashlib
import json
import sys
import time
import urllib.request

BASE = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/"
PLAN = BASE + "PLAN-steps.md"
ROWS = BASE + "battr-rows.jsonl"
ONSET_ROWS = BASE + "onset-rows.jsonl"
BATTR = BASE + "battr_logs.py"
ONSET = BASE + "onset.py"
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
        onset_rows = get(ONSET_ROWS)
        if (b"battery-r-2026, third pass" in plan and rows.count(b"\n") >= 200
                and onset_rows.count(b"\n") >= 200):
            break
        print("PLAN-steps.md or the row files on main do not look right yet", flush=True)
    except Exception as exc:
        print("not on main yet: %s" % str(exc)[:120], flush=True)
    if time.time() - t0 > WAIT_MIN * 60:
        sys.exit("PLAN-steps.md, battr-rows.jsonl and onset-rows.jsonl did not all appear on px4-log-health's main")
    time.sleep(300)

battr = get(BATTR)
if b'__version__ = "0.1.0"' not in battr:
    sys.exit("battr_logs.py on px4-log-health's main is not 0.1.0")
onset = get(ONSET)
if b'__version__ = "0.1.0"' not in onset:
    sys.exit("onset.py on px4-log-health's main is not 0.1.0")
gnss = get(GNSS)
if b'__version__ = "0.1.0"' not in gnss:
    sys.exit("gnss_logs.py on px4-gps-lag's main is not 0.1.0")
for name, data in (("battr_logs.py", battr), ("onset.py", onset), ("gnss_logs.py", gnss),
                   ("first-pass-rows.jsonl", rows), ("onset-rows.jsonl", onset_rows)):
    with open(name, "wb") as f:
        f.write(data)
fetched = {"px4-log-health/main/studies/battery-r-2026/battr_logs.py": {"sha256": sha(battr), "bytes": len(battr)},
           "px4-log-health/main/studies/battery-r-2026/onset.py": {"sha256": sha(onset), "bytes": len(onset)},
           "px4-log-health/main/studies/battery-r-2026/battr-rows.jsonl": {"sha256": sha(rows), "bytes": len(rows)},
           "px4-log-health/main/studies/battery-r-2026/onset-rows.jsonl": {"sha256": sha(onset_rows),
                                                                            "bytes": len(onset_rows)},
           "px4-log-health/main/studies/battery-r-2026/PLAN-steps.md": {"sha256": sha(plan), "bytes": len(plan)},
           "px4-gps-lag/main/studies/gnss-2026/gnss_logs.py": {"sha256": sha(gnss), "bytes": len(gnss)}}
for local in ("PLAN-steps.md", "steps.py", "test_steps.py", "job_fetch_steps.py"):
    data = open(local, "rb").read()
    fetched["local/" + local] = {"sha256": sha(data), "bytes": len(data)}
with open("job-fetched-steps.json", "w") as f:
    json.dump(fetched, f, indent=1)
print(json.dumps(fetched, indent=1), flush=True)

"""For the px4-log-health magcurrent-2026 job (published with the study's plan). Waits until
px4-log-health's main has studies/magcurrent-2026/PLAN.md, then downloads the log reader the
census reuses (gnss_logs.py 0.1.0 from px4-gps-lag's studies/gnss-2026) and records sha256 hashes
(line endings normalised) of it and of the study's own files in job-fetched.json. Exits non-zero
if the plan does not appear within the wait or a version is not the planned one.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import hashlib
import json
import sys
import time
import urllib.request

PLAN = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/magcurrent-2026/PLAN.md"
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
        if b"magcurrent-2026" in plan:
            break
        print("PLAN.md on main does not look like the plan", flush=True)
    except Exception as exc:
        print("PLAN.md not on main yet: %s" % str(exc)[:120], flush=True)
    if time.time() - t0 > WAIT_MIN * 60:
        sys.exit("studies/magcurrent-2026/PLAN.md did not appear on px4-log-health's main")
    time.sleep(300)

gnss = get(GNSS)
if b'__version__ = "0.1.0"' not in gnss:
    sys.exit("gnss_logs.py on px4-gps-lag's main is not 0.1.0")
with open("gnss_logs.py", "wb") as f:
    f.write(gnss)
fetched = {"px4-gps-lag/main/studies/gnss-2026/gnss_logs.py": {"sha256": sha(gnss), "bytes": len(gnss)},
           "px4-log-health/main/studies/magcurrent-2026/PLAN.md": {"sha256": sha(plan), "bytes": len(plan)}}
for local in ("PLAN.md", "mag_logs.py", "test_mag_logs.py", "job_fetch.py"):
    data = open(local, "rb").read()
    fetched["local/" + local] = {"sha256": sha(data), "bytes": len(data)}
with open("job-fetched.json", "w") as f:
    json.dump(fetched, f, indent=1)
print(json.dumps(fetched, indent=1), flush=True)

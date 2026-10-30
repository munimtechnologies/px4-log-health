"""Wait (up to 60 min) for studies/fleet-health-2026/GRADE.md and logs.jsonl on
px4-log-health's main, then fetch gps_lag.py (0.1.0), fleet.py (0.1.1) and
test_gps_lag.py from px4-gps-lag's main unchanged, save the study's
logs.jsonl as health-logs.jsonl, and note whether this folder's health.py,
fleet_health.py and test_health.py match main's (they will not until 0.2.0
is published). Writes fetched.json.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request

RAW = "https://raw.githubusercontent.com/munimtechnologies/%s/main/"
HERE = RAW % "px4-log-health"
LAG = RAW % "px4-gps-lag"
WAIT_FOR = ("studies/fleet-health-2026/GRADE.md", "studies/fleet-health-2026/logs.jsonl")


def get(url):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def sha(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


fetched = {}
for path in WAIT_FOR:
    for attempt in range(41):
        try:
            fetched[path] = {"on_main": True, "sha256": sha(get(HERE + path)), "waited_s": attempt * 90}
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 404 or attempt == 40:
                sys.exit("%s is not on main (%s)" % (path, exc.code))
            print("%s not on main yet; waiting 90 s" % path, flush=True)
            time.sleep(90)

for name, version in (("gps_lag.py", "0.1.0"), ("fleet.py", "0.1.1"), ("test_gps_lag.py", None)):
    data = get(LAG + name)
    if version and ('__version__ = "%s"' % version).encode() not in data:
        sys.exit("px4-gps-lag's %s on main is not %s" % (name, version))
    with open(name, "wb") as f:
        f.write(data)
    fetched["px4-gps-lag/" + name] = {"from_main": True, "sha256": sha(data)}

rows = get(HERE + "studies/fleet-health-2026/logs.jsonl")
with open("health-logs.jsonl", "wb") as f:
    f.write(rows)
fetched["studies/fleet-health-2026/logs.jsonl"]["lines"] = rows.count(b"\n")

for name in ("health.py", "fleet_health.py", "test_health.py"):
    with open(name, "rb") as f:
        local = sha(f.read())
    try:
        remote = sha(get(HERE + name))
    except urllib.error.HTTPError as exc:
        remote = "missing (%s)" % exc.code
    fetched[name] = {"local_sha256": local, "main_sha256": remote, "same_as_main": local == remote}

with open("fetched.json", "w") as f:
    json.dump(fetched, f, indent=1)
print(json.dumps(fetched), flush=True)

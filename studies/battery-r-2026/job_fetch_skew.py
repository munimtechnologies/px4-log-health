"""For the px4-log-health battery-r-2026 timing-offset step (published with PLAN-skew.md). Waits until
px4-log-health's main has studies/battery-r-2026/PLAN-skew.md, then downloads replay.py 0.1.0 and
sim.py 0.1.0 from main and checks each by hash (the exact files the replay and simulation steps were
graded with). Records sha256 hashes (line endings normalised) in job-fetched-skew.json. Also saves,
with line numbers, the lines of PX4's battery estimator the hypothesis rests on and the lines of two
battery drivers that show how voltage and current are read (px4-skew-lines.txt), for reading only.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import hashlib
import json
import sys
import time
import urllib.request

BASE = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/"
PLAN = BASE + "PLAN-skew.md"
PINNED = {
    "replay.py": {"blob": "4199dd45a5e7cae90ed81fc157cf2cc297511936", "sha256": None},
    "sim.py": {"blob": "74ecc4bde47592cd04e4fe8b929a787edced36ac",
               "sha256": "91a0aa7062fea457c550fe78f0256f76b08f105c34580844a26408111bd15ccd"},
}
PX4 = "https://raw.githubusercontent.com/PX4/PX4-Autopilot/main/"
PX4_COMMIT = "https://api.github.com/repos/PX4/PX4-Autopilot/commits?per_page=1"
SOURCES = (
    ("src/lib/battery/battery.cpp", ("updateInternalResistanceEstimation", "_RLS_est", "_voltage_prediction",
                                     "estimation_covariance_temp_norm", "i_overwrite", "updateVoltage",
                                     "updateCurrent", "updateBatteryStatus(")),
    ("src/modules/battery_status/analog_battery.cpp", ("updateBatteryStatusADC", "voltage_raw", "current_raw",
                                                       "updateVoltage", "updateCurrent", "updateBatteryStatus")),
    ("src/drivers/power_monitor/ina226/ina226.cpp", ("INA226_REG_", "_bus_voltage", "_current", "_shunt",
                                                     "updateVoltage", "updateCurrent", "INA226_CONFIG",
                                                     "AVERAGES", "CONVERSION")),
)
WAIT_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0


def get(url):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "User-Agent": "px4-log-health-skew (MV2 of Munim, Inc.)"})
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
        if b"battery-r-2026, timing offset" in plan:
            break
        print("PLAN-skew.md on main does not look right yet", flush=True)
    except Exception as exc:
        print("not on main yet: %s" % str(exc)[:120], flush=True)
    if time.time() - t0 > WAIT_MIN * 60:
        sys.exit("PLAN-skew.md did not appear on px4-log-health's main")
    time.sleep(300)

fetched = {"px4-log-health/main/studies/battery-r-2026/PLAN-skew.md": {"sha256": sha(plan), "bytes": len(plan)}}
for name, pin in PINNED.items():
    data = get(BASE + name)
    blob = git_blob(data)
    ok = blob == pin["blob"] or (pin["sha256"] is not None and sha(data) == pin["sha256"])
    if not ok:
        sys.exit("%s on px4-log-health's main is not the pinned file (blob %s, sha256 %s)" % (name, blob, sha(data)))
    with open(name, "wb") as f:
        f.write(data)
    fetched["px4-log-health/main/studies/battery-r-2026/" + name] = {"sha256": sha(data), "bytes": len(data),
                                                                    "git_blob": blob}
for local in ("PLAN-skew.md", "skew.py", "test_skew.py", "job_fetch_skew.py"):
    data = open(local, "rb").read()
    fetched["local/" + local] = {"sha256": sha(data), "bytes": len(data)}

out = []
commit = None
try:
    commit = json.loads(get(PX4_COMMIT).decode("utf-8"))[0]["sha"]
except Exception as exc:
    print("could not read the commit: %s" % str(exc)[:120], flush=True)
out.append("PX4-Autopilot main at commit %s, fetched %s UTC" % (commit, time.strftime("%Y-%m-%d %H:%M", time.gmtime())))
for path, keys in SOURCES:
    try:
        src = get(PX4 + path)
    except Exception as exc:
        out.append("")
        out.append("== %s: not readable (%s)" % (path, str(exc)[:120]))
        continue
    fetched["PX4/PX4-Autopilot/main/" + path] = {"sha256": sha(src), "bytes": len(src), "commit": commit}
    out.append("")
    out.append("== %s (sha256 %s); lines containing: %s" % (path, sha(src), ", ".join(keys)))
    in_fn = False
    for n, line in enumerate(src.decode("utf-8", "replace").splitlines(), 1):
        if "void Battery::updateInternalResistanceEstimation" in line:
            in_fn = True
        if in_fn or any(k in line for k in keys):
            out.append("%5d: %s" % (n, line.rstrip()))
        if in_fn and line.startswith("}"):
            in_fn = False
with open("px4-skew-lines.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")

with open("job-fetched-skew.json", "w") as f:
    json.dump(fetched, f, indent=1)
print(json.dumps(fetched, indent=1), flush=True)

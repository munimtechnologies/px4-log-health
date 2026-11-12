"""For the px4-log-health battery-r-2026 tool check (published with PLAN-toolcheck.md). Waits until
px4-log-health's main has studies/battery-r-2026/PLAN-toolcheck.md, then downloads replay.py 0.1.0
and sim.py 0.1.0 from main and checks their git blob hashes. Downloads PX4-Autopilot's
src/lib/battery/int_res_est_replay.py, battery.h and battery.cpp from main (saved as
px4_int_res_est_replay.py, px4_battery.h, px4_battery.cpp), records sha256 hashes (line endings
normalised) and the last commit touching each in job-fetched-toolcheck.json, and saves the lines
the check relies on, with line numbers, as px4-tool-lines.txt.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import hashlib
import json
import sys
import time
import urllib.request

BASE = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/"
PLAN = BASE + "PLAN-toolcheck.md"
PINNED = {"replay.py": "4199dd45a5e7cae90ed81fc157cf2cc297511936",
          "sim.py": "74ecc4bde47592cd04e4fe8b929a787edced36ac"}
PX4_RAW = "https://raw.githubusercontent.com/PX4/PX4-Autopilot/main/src/lib/battery/"
PX4_COMMITS = "https://api.github.com/repos/PX4/PX4-Autopilot/commits?path=src/lib/battery/%s&per_page=1"
PX4_FILES = {"int_res_est_replay.py": "px4_int_res_est_replay.py", "battery.h": "px4_battery.h",
             "battery.cpp": "px4_battery.cpp"}
KEYS = {
    "int_res_est_replay.py": ("import ", "lam", "np.diag", "theta = np.array", "0.001", "linalg.norm",
                              "for index in range", "rls_update(", "internal_resistance_stable[index]",
                              "add_argument", "FLT_EPSILON", "I[index]"),
    "battery.h": ("LAMBDA", "R_DEFAULT", "OCV_DEFAULT", "OCV_COVARIANCE", "R_COVARIANCE"),
    "battery.cpp": ("math::max(_RLS_est(1)", "FLT_EPSILON", "resetInternalResistanceEstimation(",
                    "updateInternalResistanceEstimation(", "LAMBDA"),
}
WAIT_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0


def get(url):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "User-Agent": "px4-log-health-toolcheck (MV2 of Munim, Inc.)"})
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
        if b"battery-r-2026, tool check" in plan:
            break
        print("PLAN-toolcheck.md on main does not look right yet", flush=True)
    except Exception as exc:
        print("not on main yet: %s" % str(exc)[:120], flush=True)
    if time.time() - t0 > WAIT_MIN * 60:
        sys.exit("PLAN-toolcheck.md did not appear on px4-log-health's main")
    time.sleep(300)

fetched = {"px4-log-health/main/studies/battery-r-2026/PLAN-toolcheck.md": {"sha256": sha(plan), "bytes": len(plan)}}
for name, blob in PINNED.items():
    data = get(BASE + name)
    if git_blob(data) != blob:
        sys.exit("%s on px4-log-health's main is not blob %s (got %s)" % (name, blob, git_blob(data)))
    with open(name, "wb") as f:
        f.write(data)
    fetched["px4-log-health/main/studies/battery-r-2026/" + name] = {"sha256": sha(data), "bytes": len(data),
                                                                     "git_blob": blob}
for local in ("PLAN-toolcheck.md", "toolcheck.py", "test_toolcheck.py", "job_fetch_toolcheck.py"):
    data = open(local, "rb").read()
    fetched["local/" + local] = {"sha256": sha(data), "bytes": len(data)}

out = ["Lines of PX4-Autopilot (BSD-3-Clause, Copyright PX4 Development Team) that the tool check relies on,",
       "from main, fetched %s UTC." % time.strftime("%Y-%m-%d %H:%M", time.gmtime())]
for name, local in PX4_FILES.items():
    src = get(PX4_RAW + name)
    with open(local, "wb") as f:
        f.write(src)
    commit = None
    try:
        commit = json.loads(get(PX4_COMMITS % name).decode("utf-8"))[0]["sha"]
    except Exception as exc:
        print("could not read the last commit of %s: %s" % (name, str(exc)[:120]), flush=True)
    fetched["PX4/PX4-Autopilot/main/src/lib/battery/" + name] = {"sha256": sha(src), "git_blob": git_blob(src),
                                                                  "bytes": len(src), "last_commit": commit}
    out.append("")
    out.append("== src/lib/battery/%s (git blob %s, last commit %s)" % (name, git_blob(src), commit))
    lines = src.decode("utf-8", "replace").splitlines()
    in_fn = False
    for n, line in enumerate(lines, 1):
        starts = ("def rls_update" in line or "void Battery::updateInternalResistanceEstimation" in line
                  or "void Battery::resetInternalResistanceEstimation" in line)
        if starts:
            in_fn = True
        elif in_fn and line.strip() and not line[0].isspace() and line.strip() not in ("{", "}"):
            in_fn = False
            out.append("")
        if in_fn or any(k in line for k in KEYS[name]):
            out.append("%5d: %s" % (n, line.rstrip()))
        if in_fn and line.startswith("}"):
            in_fn = False
            out.append("")
with open("px4-tool-lines.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
with open("job-fetched-toolcheck.json", "w") as f:
    json.dump(fetched, f, indent=1)
print(json.dumps(fetched, indent=1), flush=True)
print("\n".join(out), flush=True)

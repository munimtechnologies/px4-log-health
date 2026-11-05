"""For the checkup-v034 job only. Runs the whole checkup (0.3.4) on the two public logs attached
to PX4-Autopilot PR #24831 (logs_dynmodel.zip: one vehicle, u-blox F9P, GPS_UBX_DYNMODEL 6 and 7),
the logs px4-gps-lag's pre-registered paired check graded. Checks each report against the paired
rows: the lag within 0.11 ms, the dynamic model line on the model-6 log only, and the receiver read
as a serial u-blox F9P. Writes <out>/dynmodel-report.md, dynmodel-rows.jsonl, dynmodel-smoke.json.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import hashlib
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile

import checkup

ZIP = ("https://github.com/user-attachments/files/20456409/logs_dynmodel.zip",
       "aa6997a9754d4a11149bb4aa23f9721e16450eba4dc4808c0644ed2e04343b49")
# file, GPS_UBX_DYNMODEL, lag_ms (px4-gps-lag studies/dynmodel-2026/paired/paired-rows.jsonl)
EXPECT = {"log_8_2025-5-27-12-55-02.ulg": (6, 214.2), "log_5_2025-5-27-11-50-34.ulg": (7, 110.5)}
TMP = "/tmp/dynmodel"


def main(out):
    req = urllib.request.Request(ZIP[0], headers={"User-Agent": "px4-log-health/%s (MV2 of Munim, Inc.)"
                                                  % checkup.__version__})
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = resp.read()
    problems = []
    if hashlib.sha256(data).hexdigest() != ZIP[1]:
        problems.append("zip sha256 changed")
    os.makedirs(TMP, exist_ok=True)
    paths = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            base = os.path.basename(info.filename.replace("\\", "/"))
            if base in EXPECT:
                path = os.path.join(TMP, base)
                with z.open(info) as s, open(path, "wb") as d:
                    shutil.copyfileobj(s, d)
                paths.append(path)
    md, js = os.path.join(out, "dynmodel-report.md"), os.path.join(out, "dynmodel-rows.jsonl")
    if os.path.exists(md):
        os.remove(md)
    code = checkup.main(sorted(paths) + ["--md", md, "--json", js])
    with open(js, encoding="utf-8") as f:
        reports = {os.path.basename(r["log"]): r for r in (json.loads(line) for line in f)}
    with open(md, encoding="utf-8") as f:
        text = f.read()
    rows = []
    for name, (model, lag_ms) in sorted(EXPECT.items()):
        r = reports.get(name)
        if r is None:
            problems.append("%s: no report" % name)
            continue
        dm = r.get("gps_dynmodel") or {}
        lag = (r.get("gps") or {}).get("lag") or {}
        section = text.split("## %s" % name, 1)[1].split("\n## ", 1)[0] if ("## %s" % name) in text else ""
        rows.append({"file": name, "dynmodel": dm, "lag_ms": lag.get("lag_ms"),
                     "status": (r.get("gps_verdict") or {}).get("status"),
                     "note_in_report": "**u-blox dynamic model 6.**" in section})
        if dm.get("value") != model or dm.get("devtype") != "0xAA" or dm.get("note") != (model == 6):
            problems.append("%s: dynmodel %s" % (name, dm))
        if rows[-1]["note_in_report"] != (model == 6):
            problems.append("%s: note shown %s" % (name, rows[-1]["note_in_report"]))
        if not lag.get("passed") or abs(lag["lag_ms"] - lag_ms) > 0.11:
            problems.append("%s: lag %s, paired %s" % (name, lag.get("lag_ms"), lag_ms))
    for p in paths:
        os.remove(p)
    result = {"checkup_exit": code, "rows": rows, "problems": problems}
    with open(os.path.join(out, "dynmodel-smoke.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, default=str)
    print(json.dumps(result, indent=1, default=str), flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))

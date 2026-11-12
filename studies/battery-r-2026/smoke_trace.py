"""Smoke run of battr_trace.py's command line on two small public sample logs from pyulog's own test
folder on GitHub (not flight logs of the study; they predate the logged prediction fields, so the
expected outcome is a clean 'stopped' report, not a crash). Writes smoke-trace.json to the out dir.
By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.

    python smoke_trace.py OUT_DIR
"""

import json
import os
import sys
import traceback
import urllib.request

import battr_trace as tc

LOGS = ("https://raw.githubusercontent.com/PX4/pyulog/main/test/sample_log_small.ulg",
        "https://raw.githubusercontent.com/PX4/pyulog/main/test/sample_px4_events.ulg")


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    res = []
    for url in LOGS:
        name = url.rsplit("/", 1)[1]
        rec = {"log": url}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "px4-log-health-trace (MV2 of Munim, Inc.)"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(name, "wb") as f:
                f.write(resp.read())
            js = os.path.join(out_dir, "smoke-" + name + ".json")
            rec["exit"] = tc.main([name, "--json", js])
            with open(js, encoding="utf-8") as f:
                rep = json.load(f)
            rec["trace"] = rep.get("trace")
            rec["stop"] = rep.get("stop")
            rec["samples"] = rep.get("samples")
            rec["n_cells"] = rep.get("n_cells")
            rec["gates"] = rep.get("gates")
        except Exception:
            rec["error"] = traceback.format_exc()[-1500:]
        finally:
            if os.path.exists(name):
                os.remove(name)
        res.append(rec)
        print(json.dumps(rec), flush=True)
    with open(os.path.join(out_dir, "smoke-trace.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(res, indent=1) + "\n")
    return 1 if any("error" in r for r in res) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "out"))

"""smoke.py: health.py 0.2.0 on three real logs from fleet-health-2026.

Picks, from the study's logs.jsonl, the first read log of at most 15 MB from
PX4 v1.12 or older, the first from v1.13 or newer whose receiver reported
jamming state 2, and the first other v1.13+ log. Each is downloaded (10 s
apart, stop on 403/444), measured with health.py 0.2.0 and deleted. Every
figure 0.1.0 recorded must come out the same, the release must match the one
gps_lag.py recorded, and the old log must not be banded.

    python smoke.py health-logs.jsonl out/rescored/baseline.json out/smoke.jsonl

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

import json
import os
import sys
import time

import fleet
import health

SAME = ("flight_s", "vib_median", "vib_p95", "clip_count", "jam_median", "jam_state_max",
        "spoof_state_max", "battery_warning_max", "failsafe_in_flight", "sat_share")


def pick(rows):
    ok = [r for r in rows if r.get("health_ok") and (r.get("size_mb") or 99) <= 15]

    def units(r):
        return health.vib_units((r.get("lag") or {}).get("ver_sw_release"))
    old = [r for r in ok if units(r) == "m/s"]
    warn = [r for r in ok if units(r) == "m/s^2" and r.get("jam_state_max") == 2]
    new = [r for r in ok if units(r) == "m/s^2" and r not in warn[:1]]
    return [x[0] for x in (old, warn, new) if x]


def main(rows_path, baseline_path, out_path):
    with open(rows_path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    base = health.load_baseline(baseline_path)
    import requests
    session = requests.Session()
    session.headers["User-Agent"] = fleet.USER_AGENT.replace("px4-gps-lag/%s" % fleet.__version__,
                                                             "px4-log-health/%s" % health.__version__)
    problems = []
    path = "/tmp/px4-log-health-smoke.ulg"
    with open(out_path, "w", encoding="utf-8") as out:
        for i, old in enumerate(pick(rows)):
            if i:
                time.sleep(10)
            got = fleet.download(session, old["log_id"], path)
            if got != "ok":
                problems.append("%s: download %s" % (old["log_id"], got))
                continue
            new = health.measure_health(health.read_health(path))
            os.remove(path)
            new["fleet_place"] = health.fleet_place(new, base)
            diffs = {k: (old.get(k), new.get(k)) for k in SAME if old.get(k) != new.get(k)}
            want = (old.get("lag") or {}).get("ver_sw_release")
            if new.get("release") != want:
                diffs["release"] = (want, new.get("release"))
            if health.vib_units(want) == "m/s" and new.get("vib_band") is not None:
                diffs["vib_band"] = ("none for an old release", new.get("vib_band"))
            if diffs:
                problems.append("%s: %s" % (old["log_id"], diffs))
            line = {"log_id": old["log_id"], "release": new.get("release"), "vib_units": new.get("vib_units"),
                    "vib_band": new.get("vib_band"), "flags": new.get("flags"), "notes": new.get("notes"),
                    "fleet_place": new["fleet_place"], "same_as_0_1_0": not diffs}
            out.write(json.dumps(line) + "\n")
            print(json.dumps(line), flush=True)
    if problems:
        print("\n".join(problems), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))

"""Smoke test for compass.py on real public logs: six logs from the magcurrent-2026 census are
downloaded again from Flight Review and checked with compass.py. Each must give the census's own
figures (measurable or not, coupling % within 0.001, class). Writes smoke-compass.json and the
text each log gets to --out-dir; exits 1 on any mismatch.
By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0."""

import gzip
import json
import os
import sys
import tempfile
import time

import requests

import compass as cp

URL = "https://review.px4.io/download?log=%s"
UA = "px4-log-health-compass-smoke/%s (MV2 of Munim, Inc.; mv2@munimtech.com)" % cp.__version__
MAX_BYTES = 100 << 20
# (census order, log id, expected) from studies/magcurrent-2026/mag-rows.jsonl
CASES = (
    (1, "0d86be6a-af24-415f-b499-c1d0de592050", {"measurable": True, "pct": 2.689, "class": "weak"}),
    (2, "10457c04-f8e9-425c-9ad9-35d404e95439", {"measurable": True, "pct": 17.015, "class": "strong"}),
    (3, "210d957c-f459-430f-9af2-3a5511808ad8", {"read": "no vehicle_magnetometer data"}),
    (23, "0b191c22-16b6-405b-9658-00e825de23b4", {"measurable": True, "pct": 29.303, "class": "strong"}),
    (47, "ab4872b7-ae2d-43f2-bc9c-35057790f5d0", {"measurable": True, "pct": 98.105, "class": "strong"}),
    (117, "05563f29-7ca3-4e49-9fe5-a5a0f7dc35b6", {"measurable": True, "pct": 35.208, "class": "strong"}),
)


def download(log_id, path):
    with requests.get(URL % log_id, headers={"User-Agent": UA}, stream=True, timeout=300) as r:
        r.raise_for_status()
        total = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("log over 100 MB")
                f.write(chunk)
    with open(path, "rb") as f:
        head = f.read(2)
    if head == b"\x1f\x8b":
        with gzip.open(path, "rb") as g:
            data = g.read()
        with open(path, "wb") as f:
            f.write(data)
    return total


def compare(res, exp):
    m = res.get("measure") or {}
    if "read" in exp:
        return m.get("read") == exp["read"]
    if not m.get("measurable"):
        return False
    pct = m["coupling"]["pct"]
    return abs(pct - exp["pct"]) <= 0.001 and m.get("class") == exp["class"]


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(tempfile.gettempdir(), "compass-smoke.ulg")
    rows, ok_all = [], True
    for i, (order, log_id, exp) in enumerate(CASES):
        if i:
            time.sleep(6)
        row = {"order": order, "log_id": log_id, "expected": exp}
        try:
            row["bytes"] = download(log_id, tmp)
            res = cp.check(tmp)
            row.update({"level": res["level"], "text": res["text"], "coupling_pct": res["coupling_pct"],
                        "class": res["class"], "fleet_place": res["fleet_place"],
                        "cal_mag_comp_typ": res["cal_mag_comp_typ"],
                        "read": (res.get("measure") or {}).get("read"),
                        "why_not": (res.get("measure") or {}).get("why_not")})
            row["same_as_census"] = compare(res, exp)
        except Exception as exc:
            row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
            row["same_as_census"] = False
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        ok_all = ok_all and row["same_as_census"]
        rows.append(row)
        print(json.dumps(row, default=cp._plain), flush=True)
    summary = {"compass": cp.__version__, "cases": len(rows),
               "same_as_census": sum(1 for r in rows if r["same_as_census"]), "ok": ok_all, "rows": rows}
    with open(os.path.join(out_dir, "smoke-compass.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, default=cp._plain)
    with open(os.path.join(out_dir, "smoke-compass.txt"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write("census order %s, log %s\n  [%s] %s\n\n" % (r["order"], r["log_id"], r.get("level"),
                                                               r.get("text") or r.get("error")))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "out"))

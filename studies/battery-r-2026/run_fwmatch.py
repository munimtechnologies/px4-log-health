# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name.
"""Driver for PLAN-fwmatch.md: replay flight A's battery rows under each setting and grade the predictions.

Usage: python run_fwmatch.py --data-dir DIR --out-dir DIR
Reads only nov14-flight-a-100hz-battery.csv (checked by sha256). Writes fwmatch.json with derived numbers only.
"""

import argparse
import csv
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fwmatch as fm  # noqa: E402

FILE = "nov14-flight-a-100hz-battery.csv"
SHA256 = "07ed7143e8ab7775e51e69851558b429fd3787fd351b86c305cedb31ea298a4c"
N_CELLS = 4
RUNS = [("firmware", "seed"), ("replay_tool", "first"), ("replay_tool", "seed"),
        ("tool_constants_fw_handling", "seed"), ("fw_constants_tool_handling", "seed")]


def grade(results):
    fw = results["firmware/seed"]
    tool = results["replay_tool/first"]
    g = {
        "F1_fw_decision_agree_ge_0.99": fw["decision_agree_share"] is not None and fw["decision_agree_share"] >= 0.99,
        "F2_fw_airborne_accepts_equal": fw["airborne_accepted_model"] == fw["airborne_accepted_logged"],
        "F3_fw_airborne_zero_equal": fw["airborne_zero_published_model"] == fw["airborne_zero_published_logged"],
        "F4_fw_pred_median_lt_1mV": fw["pred_abs_err_median_v"] is not None and fw["pred_abs_err_median_v"] < 0.001,
        "T1_tool_decision_agree_lt_0.95": tool["decision_agree_share"] is not None
        and tool["decision_agree_share"] < 0.95,
        "T2_tool_airborne_zero_lt_3000": tool["airborne_zero_published_model"] < 3000,
    }
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    out_path = os.path.join(a.out_dir, "fwmatch.json")
    path = os.path.join(a.data_dir, FILE)
    if not os.path.isfile(path):
        listing = sorted(os.listdir(a.data_dir)) if os.path.isdir(a.data_dir) else []
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"error": "data file missing", "data_dir_listing": listing}, f, indent=1)
        print("fwmatch: data file missing:", path, "listing:", listing)
        return 2
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest != SHA256:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"error": "sha256 mismatch", "sha256": digest}, f, indent=1)
        print("fwmatch: sha256 mismatch", digest)
        return 3
    with open(path, newline="", encoding="utf-8") as f:
        rows = fm.parse_battery_csv_rows(csv.DictReader(f))
    results = {}
    for setting, start in RUNS:
        results[setting + "/" + start] = fm.compare(rows, N_CELLS, setting, start)
    seed = fm.find_seed(rows, N_CELLS)
    report = {
        "fwmatch_version": fm.__version__,
        "file": FILE,
        "sha256": digest,
        "rows_parsed": len(rows),
        "seed_row": seed,
        "seed_t_s": rows[seed]["t_us"] / 1e6 if seed is not None else None,
        "results": results,
        "grade": grade(results),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True)
    print(json.dumps({"rows_parsed": len(rows), "seed_row": seed, "grade": report["grade"]}, indent=1))
    for key, r in results.items():
        print(key, "agree=%s accepted_model=%s logged=%s airborne_zero_model=%s logged=%s pred_median=%s"
              % (r["decision_agree_share"], r["accepted_total_model"], r["accepted_total_logged"],
                 r["airborne_zero_published_model"], r["airborne_zero_published_logged"],
                 r["pred_abs_err_median_v"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

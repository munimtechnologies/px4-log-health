# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name.
"""Driver for PLAN-apcompare.md: run the ArduPilot-style estimator on flights A and B and grade the predictions.

Usage: python run_apcompare.py --data-dir DIR --out-dir DIR
Reads nov14-flight-a-100hz-battery.csv (checked by sha256) and, if present, nov14-flight-b-100hz-battery.csv
(sha256 recorded). Writes apcompare.json with derived numbers only.
"""

import argparse
import csv
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apcompare as apc  # noqa: E402

FILE_A = "nov14-flight-a-100hz-battery.csv"
FILE_B = "nov14-flight-b-100hz-battery.csv"
SHA256_A = "07ed7143e8ab7775e51e69851558b429fd3787fd351b86c305cedb31ea298a4c"
N_CELLS = 4


def load(path):
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(path, newline="", encoding="utf-8") as f:
        rows = apc.parse_rows(csv.DictReader(f))
    return digest, rows


def analyse(rows):
    air = [r for r in rows if r["landed"] == 0]
    return {
        "rows": len(rows),
        "airborne_rows": len(air),
        "airborne_rows_logged_px4_zero": sum(1 for r in air if r["logged_r"] == 0.0),
        "M_10hz_block_means": apc.summarize(apc.run(apc.block_means(rows)), N_CELLS),
        "S_100hz_every_row": apc.summarize(apc.run(rows), N_CELLS),
    }


def in_range(x, lo, hi):
    return x is not None and lo <= x <= hi


def grade(a, b):
    m, s = a["M_10hz_block_means"], a["S_100hz_every_row"]
    g = {
        "D1_A_airborne_rows_3685_all_px4_zero": a["airborne_rows"] == 3685
        and a["airborne_rows_logged_px4_zero"] == 3685,
        "A1_M_positive_before_takeoff": bool(m["positive_before_takeoff"]),
        "A2_M_final_airborne_3_to_20_mohm": in_range(m["airborne_mohm_per_cell_final"], 3.0, 20.0),
        "A3_M_median_airborne_4.25_to_17_mohm": in_range(m["airborne_mohm_per_cell_median"], 4.25, 17.0),
        "A4_S_discard_share_ge_0.40": s["discard_share_airborne"] is not None
        and s["discard_share_airborne"] >= 0.40,
        "A5_S_discard_share_gt_M": s["discard_share_airborne"] is not None
        and m["discard_share_airborne"] is not None
        and s["discard_share_airborne"] > m["discard_share_airborne"],
    }
    if b is not None:
        g["B1_M_final_airborne_3_to_20_mohm"] = in_range(
            b["M_10hz_block_means"]["airborne_mohm_per_cell_final"], 3.0, 20.0)
    else:
        g["B1_M_final_airborne_3_to_20_mohm"] = None
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, "apcompare.json")
    path_a = os.path.join(args.data_dir, FILE_A)
    if not os.path.isfile(path_a):
        listing = sorted(os.listdir(args.data_dir)) if os.path.isdir(args.data_dir) else []
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"error": "data file missing", "data_dir_listing": listing}, f, indent=1)
        print("apcompare: data file missing:", path_a, "listing:", listing)
        return 2
    digest_a, rows_a = load(path_a)
    if digest_a != SHA256_A:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"error": "sha256 mismatch", "sha256": digest_a}, f, indent=1)
        print("apcompare: sha256 mismatch", digest_a)
        return 3
    a = analyse(rows_a)
    b = None
    digest_b = None
    path_b = os.path.join(args.data_dir, FILE_B)
    if os.path.isfile(path_b):
        digest_b, rows_b = load(path_b)
        b = analyse(rows_b)
    report = {
        "apcompare_version": apc.__version__,
        "files": {FILE_A: digest_a, FILE_B: digest_b},
        "flight_a": a,
        "flight_b": b,
        "grade": grade(a, b),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True)
    print(json.dumps(report["grade"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

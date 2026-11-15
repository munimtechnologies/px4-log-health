# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name.
"""fwmatch 0.1.0: replay PX4's battery internal-resistance RLS under firmware settings or under the settings of
PX4's own offline tool, and measure how well each reproduces what the firmware logged.

Two sources, both in PX4-Autopilot src/lib/battery/ (PX4 code is BSD-3-Clause; this re-implementation is original
and Apache-2.0):

  firmware     battery.cpp / battery.h (revision 6ea3539157ca358c70a515878b77077af7d4611d; the same logic on main
               on 2026-11-15): LAMBDA 0.95, initial covariance diag(1.5 n, 0.1 n), update only when
               I > FLT_EPSILON, accept only if the candidate norm is strictly below the stored norm,
               OCV seeded with the measured voltage at reset, norm sqrt(P00^2 + 2 P10^2 + P11^2).
  replay_tool  int_res_est_replay.py (last changed in fd8df2e84d04ac9fff36ece9eda1c5a0a432bc69): default
               forgetting factor 0.99, initial covariance diag(1.2 n, 0.1 n), every sample updated (no current
               test), update rejected only if norm(P) < norm(P_temp) (ties accepted), OCV seeded with
               V0 + 0.005 n I0, numpy Frobenius norm.

Two mixed settings separate the effect of the constants from the effect of the sample handling.
Python floats are float64; PX4 runs float32.

Input rows are dicts with float values: t_us, v, i, norm (logged estimation_covariance_norm), pred (logged
voltage_prediction), landed (1/0). Output is derived numbers only.
"""

import math

__version__ = "0.1.0"

FLT_EPSILON = 1.1920928955078125e-07
R_DEFAULT = 0.005   # [Ohm] per cell, the same in both sources
OCV_DEFAULT = 4.2   # [V] per cell (firmware seeding before the first reset)

SETTINGS = {
    "firmware": {"lam": 0.95, "ocv_cov": 1.5, "r_cov": 0.1, "skip_low_current": True,
                 "accept_ties": False, "ocv_init": "voltage", "norm": "px4"},
    "replay_tool": {"lam": 0.99, "ocv_cov": 1.2, "r_cov": 0.1, "skip_low_current": False,
                    "accept_ties": True, "ocv_init": "voltage_plus_ir", "norm": "frobenius"},
    "tool_constants_fw_handling": {"lam": 0.99, "ocv_cov": 1.2, "r_cov": 0.1, "skip_low_current": True,
                                   "accept_ties": False, "ocv_init": "voltage", "norm": "px4"},
    "fw_constants_tool_handling": {"lam": 0.95, "ocv_cov": 1.5, "r_cov": 0.1, "skip_low_current": False,
                                   "accept_ties": True, "ocv_init": "voltage", "norm": "frobenius"},
}


def matrix_norm(p00, p01, p10, p11, kind):
    """'px4': sqrt(P00^2 + 2 P10^2 + P11^2) as in battery.cpp; 'frobenius': all four entries (numpy)."""
    if kind == "px4":
        return math.sqrt(p00 * p00 + 2.0 * p10 * p10 + p11 * p11)
    return math.sqrt(p00 * p00 + p01 * p01 + p10 * p10 + p11 * p11)


def accepts(candidate_norm, stored_norm, accept_ties):
    """Firmware: accept iff candidate < stored. Tool: reject iff stored < candidate (so ties are accepted)."""
    if accept_ties:
        return not (stored_norm < candidate_norm)
    return candidate_norm < stored_norm


def initial_norm(n_cells, setting="firmware"):
    s = SETTINGS[setting]
    return matrix_norm(s["ocv_cov"] * n_cells, 0.0, 0.0, s["r_cov"] * n_cells, s["norm"])


class Est:
    """e0 = pack OCV [V], e1 = signed pack resistance [Ohm], P (2x2), stored norm."""

    def __init__(self, n_cells, voltage_v, current_a=0.0, setting="firmware"):
        if n_cells <= 0:
            raise ValueError("n_cells must be positive")
        self.s = SETTINGS[setting]
        self.n = n_cells
        self.lam = self.s["lam"]
        if self.s["ocv_init"] == "voltage":
            self.e0 = float(voltage_v)
        else:
            self.e0 = float(voltage_v) + R_DEFAULT * n_cells * float(current_a)
        self.e1 = R_DEFAULT * n_cells
        self.p00 = self.s["ocv_cov"] * n_cells
        self.p01 = 0.0
        self.p10 = 0.0
        self.p11 = self.s["r_cov"] * n_cells
        self.norm = matrix_norm(self.p00, self.p01, self.p10, self.p11, self.s["norm"])

    def r_pub(self):
        """What PX4 publishes per cell: max(R_pack / n, 0)."""
        return max(self.e1 / self.n, 0.0)

    def step(self, voltage_v, current_a):
        """One sample. None when the setting skips it (firmware: current <= FLT_EPSILON)."""
        if self.s["skip_low_current"] and not current_a > FLT_EPSILON:
            return None
        lam = self.lam
        x0 = 1.0
        x1 = -float(current_a)
        pred = x0 * self.e0 + x1 * self.e1
        err = voltage_v - pred
        px0 = self.p00 * x0 + self.p01 * x1
        px1 = self.p10 * x0 + self.p11 * x1
        xp0 = x0 * self.p00 + x1 * self.p10
        xp1 = x0 * self.p01 + x1 * self.p11
        den = lam + (x0 * px0 + x1 * px1)
        g0 = px0 / den
        g1 = px1 / den
        q00 = (self.p00 - g0 * xp0) / lam
        q01 = (self.p01 - g0 * xp1) / lam
        q10 = (self.p10 - g1 * xp0) / lam
        q11 = (self.p11 - g1 * xp1) / lam
        qn = matrix_norm(q00, q01, q10, q11, self.s["norm"])
        ok = accepts(qn, self.norm, self.s["accept_ties"])
        if ok:
            self.e0 = self.e0 + g0 * err
            self.e1 = self.e1 + g1 * err
            self.p00, self.p01, self.p10, self.p11 = q00, q01, q10, q11
            self.norm = qn
        else:
            self.e0 = voltage_v + self.e1 * current_a
        return {"accepted": ok, "prediction": pred, "candidate_norm": qn}


def find_seed(rows, n_cells, rel_tol=1e-6):
    """Index of the last row whose logged norm equals the firmware's initial norm (the last reset), or None."""
    init = initial_norm(n_cells, "firmware")
    k0 = None
    for k, r in enumerate(rows):
        if abs(r["norm"] - init) <= rel_tol * init:
            k0 = k
    return k0


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(math.ceil(q * len(sorted_vals))) - 1))
    return sorted_vals[idx]


def replay(rows, n_cells, setting, start="seed"):
    """Run one setting over the rows.

    start='seed': begin at the last logged reset (find_seed), as the firmware state is known there.
    start='first': begin at row 0, as int_res_est_replay.py does on a whole log.
    Returns (begin_index, list of per-row records aligned with rows[begin:]).
    """
    if start == "seed":
        k0 = find_seed(rows, n_cells)
        if k0 is None:
            raise ValueError("no row with the firmware's initial covariance norm; cannot seed")
        est = Est(n_cells, rows[k0]["v"], rows[k0]["i"], setting)
        begin = k0 + 1
    else:
        est = Est(n_cells, rows[0]["v"], rows[0]["i"], setting)
        begin = 0
    recs = []
    for r in rows[begin:]:
        res = est.step(r["v"], r["i"])
        recs.append({
            "accepted": bool(res and res["accepted"]),
            "updated": res is not None,
            "prediction": None if res is None else res["prediction"],
            "norm": est.norm,
            "r_pub": est.r_pub(),
            "e1": est.e1,
        })
    return begin, recs


def logged_accepts(rows, begin):
    """Logged accept at row k: the logged norm changed from row k-1 (valid after the last reset)."""
    out = []
    for k in range(begin, len(rows)):
        prev = rows[k - 1]["norm"] if k > 0 else None
        out.append(prev is not None and rows[k]["norm"] != prev)
    return out


def compare(rows, n_cells, setting, start="seed"):
    """Derived-number comparison of one setting against the logged firmware values."""
    begin, recs = replay(rows, n_cells, setting, start)
    # Decisions are compared from the last reset onward, whatever the start, so all settings use the same rows.
    k0 = find_seed(rows, n_cells)
    cmp_from = (k0 + 1) if k0 is not None else begin
    logged = logged_accepts(rows, cmp_from)
    agree = 0
    total = 0
    first_dis = None
    norm_ok = 0
    norm_max_rel = 0.0
    pred_err = []
    airborne_rows = 0
    airborne_zero_model = 0
    airborne_zero_logged = 0
    airborne_acc_model = 0
    airborne_acc_logged = 0
    for j, k in enumerate(range(cmp_from, len(rows))):
        rec = recs[k - begin]
        row = rows[k]
        total += 1
        if rec["accepted"] == logged[j]:
            agree += 1
        elif first_dis is None:
            first_dis = row["t_us"] / 1e6
        rel = abs(rec["norm"] - row["norm"]) / row["norm"] if row["norm"] else float("inf")
        norm_max_rel = max(norm_max_rel, rel)
        if rel <= 1e-4:
            norm_ok += 1
        if row["i"] > FLT_EPSILON and rec["prediction"] is not None and row.get("pred") is not None:
            pred_err.append(abs(rec["prediction"] - row["pred"]))
        if row["landed"] == 0:
            airborne_rows += 1
            airborne_zero_model += rec["r_pub"] == 0.0
            airborne_zero_logged += row.get("r", None) == 0.0
            airborne_acc_model += rec["accepted"]
            airborne_acc_logged += logged[j]
    pred_err.sort()
    last_air = None
    for k in range(cmp_from, len(rows)):
        if rows[k]["landed"] == 0:
            last_air = k
    return {
        "setting": setting,
        "start": start,
        "params": dict(SETTINGS[setting]),
        "begin_row": begin,
        "compare_from_row": cmp_from,
        "rows_compared": total,
        "decision_agree": agree,
        "decision_agree_share": (agree / total) if total else None,
        "first_disagreement_t_s": first_dis,
        "accepted_total_model": sum(r["accepted"] for r in recs[cmp_from - begin:]),
        "accepted_total_logged": sum(logged),
        "norm_within_1e-4_share": (norm_ok / total) if total else None,
        "norm_max_rel_err": norm_max_rel,
        "pred_abs_err_median_v": _quantile(pred_err, 0.5),
        "pred_abs_err_p99_v": _quantile(pred_err, 0.99),
        "pred_abs_err_max_v": pred_err[-1] if pred_err else None,
        "pred_rows": len(pred_err),
        "airborne_rows": airborne_rows,
        "airborne_accepted_model": airborne_acc_model,
        "airborne_accepted_logged": airborne_acc_logged,
        "airborne_zero_published_model": airborne_zero_model,
        "airborne_zero_published_logged": airborne_zero_logged,
        "e1_pack_ohm_at_last_airborne_row_model": recs[last_air - begin]["e1"] if last_air is not None else None,
    }


def parse_battery_csv_rows(dict_rows):
    """Map rows of the shared battery CSV (csv.DictReader) to fwmatch rows; rows with a bad number are dropped."""
    out = []
    for d in dict_rows:
        try:
            out.append({
                "t_us": float(d["time_from_takeoff_us"]),
                "v": float(d["voltage_v"]),
                "i": float(d["current_a"]),
                "r": float(d["internal_resistance_estimate"]),
                "norm": float(d["estimation_covariance_norm"]),
                "pred": float(d["voltage_prediction"]),
                "landed": int(float(d["landed_last_reported"])),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out

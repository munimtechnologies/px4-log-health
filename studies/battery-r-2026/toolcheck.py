"""toolcheck.py: does PX4's own offline replay tool for the battery internal-resistance estimator
(src/lib/battery/int_res_est_replay.py) compute what the firmware computes? (px4-log-health study
battery-r-2026, tool check, pre-registered in studies/battery-r-2026/PLAN-toolcheck.md).

Checks six settings from the downloaded source lines, runs PX4's own `rls_update` (taken out of
the tool with ast; nothing else of the tool is run) with the firmware's settings against MV2's
port (replay.py 0.1.0), and runs the tool as shipped (forgetting factor 0.99) on one simulated
cell of sim.py 0.1.0's model.

    python toolcheck.py --out-dir out [--plan PLAN-toolcheck.md] [--wait-min 120] [--seeds 100]

Needs numpy, replay.py 0.1.0 and sim.py 0.1.0 (px4-log-health, studies/battery-r-2026) and the
files saved by job_fetch_toolcheck.py next to it.
By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import argparse
import ast
import json
import os
import re
import sys
import time
import urllib.request

import numpy as np

import replay as rp
import sim

__version__ = "0.1.0"

PLAN_URL = ("https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/"
            "battery-r-2026/PLAN-toolcheck.md")

TOOL_FILE = "px4_int_res_est_replay.py"
BATTERY_H = "px4_battery.h"
BATTERY_CPP = "px4_battery.cpp"

SEEDS = 100
SEED = [2026, 11, 12, 7]
NOISE = "typical"
LAM_TOOL = 0.99            # the tool's -l default (checked by C1)
P0_OCV_TOOL = 1.2          # per cell, the tool's initial covariance on OCV (checked by C1)
P0_R_TOOL = 0.1            # per cell
R_INIT_TOOL = 0.005        # per cell, the tool's initial R
FLOOR_TOOL = 0.001         # Ohm per cell, the tool's shown-value floor (checked by C1)
PARITY_TOL = 1e-9          # Ohm per cell
CONFIGS = ("fw32", "fw64", "T1", "T0", "T0-l95")

GRADED_FACTS = {
    "tool_lambda_default_0.99": ("tool", r"add_argument\(\s*['\"]-l['\"][^\n]*default\s*=\s*0\.99\b"),
    "tool_initial_covariance_1.2_0.1": ("tool", r"np\.diag\(\[\s*1\.2\s*\*\s*n_cells\s*,\s*0\.1\s*\*\s*n_cells\s*\]\)"),
    "tool_shown_floor_0.001": ("tool", r"max\(\s*R_est\[index\]\s*/\s*n_cells\s*,\s*0\.001\s*\)"),
    "fw_lambda_0.95": ("battery.h", r"LAMBDA\s*=\s*0\.95f"),
    "fw_ocv_covariance_1.5": ("battery.h", r"OCV_COVARIANCE\s*=\s*1\.5f"),
    "fw_published_floor_0": ("battery.cpp", r"math::max\(\s*_RLS_est\(1\)\s*/\s*_params\.n_cells\s*,\s*0\.f\s*\)"),
}
FW_CURRENT = r"if\s*\(\s*current_a\s*>\s*FLT_EPSILON\s*\)"


# ---------------------------------------------------------------- the sources

def load_rls_update(source):
    """PX4's rls_update, compiled from its own source text; nothing else of the file runs."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "rls_update":
            seg = ast.get_source_segment(source, node)
            ns = {"np": np}
            exec(compile(seg, TOOL_FILE, "exec"), ns)
            return ns["rls_update"]
    raise ValueError("no top-level rls_update in the tool")


def code_facts(tool, bat_h, bat_cpp):
    """(graded, reported): each fixed pattern found (True) or not in the downloaded text."""
    texts = {"tool": tool, "battery.h": bat_h, "battery.cpp": bat_cpp}
    graded = {k: bool(re.search(pat, texts[src])) for k, (src, pat) in GRADED_FACTS.items()}
    reported = {
        "tool_no_current_condition": (re.search(r"if[^\n]*I\[index\]\s*>", tool) is None
                                      and "FLT_EPSILON" not in tool),
        "fw_update_if_current_positive": bool(re.search(FW_CURRENT, bat_cpp)),
    }
    return graded, reported


# ---------------------------------------------------------------- the ways of running

def tool_firmware_pattern(rls_update, t_us, v, i, n_cells, lam=None):
    """PX4's rls_update (float64) with the firmware's settings and call pattern: reset to
    (V, R_DEFAULT * n) and diag(OCV_COVARIANCE, R_COVARIANCE) * n during the first 2 s, update only
    when current > FLT_EPSILON, publish max(R / n, 0) only on an accepted update.
    Returns (published, raw R per cell, accepted), arrays aligned with the input."""
    lam = rp.LAMBDA if lam is None else lam
    n = len(v)
    pub = np.empty(n)
    raw = np.empty(n)
    acc = np.zeros(n, dtype=bool)
    init_end = int(t_us[0]) + rp.INIT_US
    theta = np.array([[rp.OCV_DEFAULT * n_cells], [rp.R_DEFAULT * n_cells]])
    P = np.diag([rp.OCV_COVARIANCE * n_cells, rp.R_COVARIANCE * n_cells])
    r_pub = rp.R_DEFAULT
    for k in range(n):
        vk = float(v[k])
        ik = float(i[k])
        if int(t_us[k]) <= init_end:
            theta = np.array([[vk], [rp.R_DEFAULT * n_cells]])
            P = np.diag([rp.OCV_COVARIANCE * n_cells, rp.R_COVARIANCE * n_cells])
            r_pub = rp.R_DEFAULT
        if ik > rp.FLT_EPSILON:
            x = np.array([[1.0], [-ik]])
            res = rls_update(theta, P, x, vk, ik, lam)
            if res[1] is not P:
                acc[k] = True
                r_pub = max(float(res[0][1][0]) / n_cells, 0.0)
            theta, P = res[0], res[1]
        pub[k] = r_pub
        raw[k] = float(theta[1][0]) / n_cells
    return pub, raw, acc


def tool_shipped(rls_update, v, i, n_cells, lam=LAM_TOOL):
    """The tool's own loop: initial state from the first sample, initial covariance
    diag(1.2, 0.1) * n, every sample updated, no reset. Returns (raw R per cell, accepted); the tool
    shows max(raw, 0.001)."""
    n = len(v)
    theta = np.array([[float(v[0]) + R_INIT_TOOL * n_cells * float(i[0])], [R_INIT_TOOL * n_cells]])
    P = np.diag([P0_OCV_TOOL * n_cells, P0_R_TOOL * n_cells])
    raw = np.empty(n)
    acc = np.zeros(n, dtype=bool)
    for k in range(n):
        vk = float(v[k])
        ik = float(i[k])
        x = np.array([[1.0], [-ik]])
        res = rls_update(theta, P, x, vk, ik, lam)
        acc[k] = res[1] is not P
        theta, P = res[0], res[1]
        raw[k] = float(theta[1][0]) / n_cells
    return raw, acc


def shown(raw):
    return np.maximum(np.asarray(raw, dtype=np.float64), FLOOR_TOOL)


def parity(acc_a, acc_b, pub_a, pub_b):
    """(same accept decisions everywhere, largest |published difference|)."""
    same = bool(np.array_equal(np.asarray(acc_a, dtype=bool), np.asarray(acc_b, dtype=bool)))
    d = np.abs(np.asarray(pub_a, dtype=np.float64) - np.asarray(pub_b, dtype=np.float64))
    return same, float(np.max(d)) if d.size else 0.0


def air_share(acc, takeoff):
    """Per run: share of airborne samples with an accepted update. acc (N, S)."""
    return np.asarray(acc[takeoff:], dtype=np.float64).mean(axis=0)


# ---------------------------------------------------------------- the run

def run_all(rls_update, seeds):
    rng = np.random.default_rng(SEED)
    t, cur, volt, vm, im = sim.inputs(rng, seeds, NOISE, 0, 0)
    n_g, n_s, _ = sim.steps()
    takeoff = n_g + n_s
    n, s = vm.shape
    nc = sim.N_CELLS
    fw32 = sim.run_estimator(t, vm, im, nc)
    conf = {"fw32": {"pub": fw32["r_pub"].astype(np.float64), "raw": fw32["r_raw"].astype(np.float64),
                     "acc": fw32["accepted"].copy()}}
    for name in ("fw64", "T1", "T0", "T0-l95"):
        conf[name] = {"pub": np.empty((n, s)), "raw": np.empty((n, s)), "acc": np.zeros((n, s), dtype=bool)}
    conn = np.ones(n, dtype=bool)
    for j in range(s):
        v = vm[:, j]
        i = im[:, j]
        r = rp.replay(t, v, i, conn, nc, gated=True, dtype=np.float64)
        conf["fw64"]["pub"][:, j] = r["r_pub"]
        conf["fw64"]["raw"][:, j] = r["r_raw"]
        conf["fw64"]["acc"][:, j] = r["accepted"]
        pub, raw, acc = tool_firmware_pattern(rls_update, t, v, i, nc)
        conf["T1"]["pub"][:, j] = pub
        conf["T1"]["raw"][:, j] = raw
        conf["T1"]["acc"][:, j] = acc
        for name, lam in (("T0", LAM_TOOL), ("T0-l95", rp.LAMBDA)):
            raw, acc = tool_shipped(rls_update, v, i, nc, lam)
            conf[name]["pub"][:, j] = np.maximum(raw, 0.0)
            conf[name]["raw"][:, j] = raw
            conf[name]["acc"][:, j] = acc
        if (j + 1) % 10 == 0:
            print("runs done: %d of %d" % (j + 1, s), flush=True)
    return takeoff, conf


def summarise(conf, takeoff, graded, reported):
    s = conf["fw32"]["pub"].shape[1]
    out = {}
    per_run = [{"run": j} for j in range(s)]
    for name in CONFIGS:
        c = conf[name]
        o = sim.outcomes(c["pub"], takeoff)
        share = air_share(c["acc"], takeoff)
        rel = np.abs(o["r_end"] / sim.R0 - 1.0)
        st = {"at_takeoff": int(np.sum(o["at_takeoff"])), "in_flight": int(np.sum(o["in_flight"])),
              "r_end_mohm_quantiles": sim._quantiles([float(x) * 1000.0 for x in o["r_end"]]),
              "rel_err_quantiles": sim._quantiles([float(x) for x in rel]),
              "air_accept_share_quantiles": sim._quantiles([float(x) for x in share])}
        if name in ("T0", "T0-l95"):
            below = c["raw"][takeoff:] <= 0.0
            runs_below = np.any(below, axis=0)
            st["runs_at_or_below_0_in_air"] = int(np.sum(runs_below))
            st["smallest_shown_where_at_or_below_0_mohm"] = (
                round(float(np.min(shown(c["raw"][takeoff:])[below])) * 1000.0, 6) if np.any(below) else None)
        out[name] = st
        for j in range(s):
            per_run[j][name] = {"at_takeoff": bool(o["at_takeoff"][j]), "in_flight": bool(o["in_flight"][j]),
                                "r_end_mohm": round(float(o["r_end"][j]) * 1000.0, 6),
                                "air_accept_share": round(float(share[j]), 6)}
    k1 = 0
    worst = 0.0
    k32 = 0
    worst32 = 0.0
    k2 = 0
    s0 = air_share(conf["T0"]["acc"], takeoff)
    s32 = air_share(conf["fw32"]["acc"], takeoff)
    for j in range(s):
        same, d = parity(conf["T1"]["acc"][:, j], conf["fw64"]["acc"][:, j],
                         conf["T1"]["pub"][:, j], conf["fw64"]["pub"][:, j])
        ok = same and d <= PARITY_TOL
        k1 += ok
        worst = max(worst, d)
        per_run[j]["parity_T1_fw64"] = {"same_decisions": same, "max_abs_diff_ohm": d}
        same32, d32 = parity(conf["fw32"]["acc"][:, j], conf["fw64"]["acc"][:, j],
                             conf["fw32"]["pub"][:, j], conf["fw64"]["pub"][:, j])
        k32 += (not same32)
        worst32 = max(worst32, d32)
        per_run[j]["fw32_vs_fw64"] = {"same_decisions": same32, "max_abs_diff_ohm": d32}
        k2 += bool(s0[j] > s32[j])
    pred = {"C1": all(graded.values()),
            "P1": (100 * k1 >= 99 * s) if s else None,
            "P2": (10 * k2 >= 9 * s) if s else None}
    rep = {"code_facts_graded": graded, "code_facts_reported": reported,
           "p1_runs_in_parity": k1, "p1_runs": s, "p1_largest_diff_ohm": worst,
           "p2_runs_T0_accepts_more_in_air": k2, "p2_runs": s,
           "fw32_vs_fw64_runs_with_different_decisions": k32, "fw32_vs_fw64_largest_diff_ohm": worst32,
           "configs": out}
    return pred, rep, per_run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--plan", default="PLAN-toolcheck.md")
    ap.add_argument("--wait-min", type=float, default=120)
    ap.add_argument("--seeds", type=int, default=SEEDS)
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)
    with open(TOOL_FILE, encoding="utf-8") as f:
        tool = f.read()
    with open(BATTERY_H, encoding="utf-8") as f:
        bat_h = f.read()
    with open(BATTERY_CPP, encoding="utf-8") as f:
        bat_cpp = f.read()
    rls_update = load_rls_update(tool)

    def get_plan():
        req = urllib.request.Request(PLAN_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    if not sim.wait_for_plan(args.plan, get_plan, args.wait_min * 60):
        with open(os.path.join(args.out_dir, "toolcheck-summary.json"), "w") as f:
            json.dump({"toolcheck": __version__, "stop": "plan not on main; nothing run"}, f, indent=1)
        print("PLAN-toolcheck.md on main is not the local copy: nothing run", flush=True)
        return 3
    started = time.time()
    graded, reported = code_facts(tool, bat_h, bat_cpp)
    takeoff, conf = run_all(rls_update, args.seeds)
    pred, rep, per_run = summarise(conf, takeoff, graded, reported)
    summary = {"toolcheck": __version__, "replay": rp.__version__, "sim": sim.__version__,
               "seeds": args.seeds, "seed": SEED, "noise": NOISE, "predictions": pred, "reported": rep,
               "wall_s": round(time.time() - started, 1)}
    with open(os.path.join(args.out_dir, "toolcheck-summary.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, indent=1) + "\n")
    with open(os.path.join(args.out_dir, "toolcheck-runs.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(per_run, indent=1) + "\n")
    print(json.dumps(summary, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

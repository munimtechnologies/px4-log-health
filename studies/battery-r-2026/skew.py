"""skew.py: is a one-sample offset between the voltage and current samples at a load step enough to
drive PX4's battery internal-resistance estimate to 0? (px4-log-health study battery-r-2026,
pre-registered in studies/battery-r-2026/PLAN-skew.md.)

Reuses sim.py 0.1.0's model battery, sensors, outcomes and vectorised float32 port of PX4's update
(replay.py 0.1.0); both are fetched unchanged and checked by hash before anything runs.

    python skew.py --out-dir out [--plan PLAN-skew.md] [--wait-min 30] [--seeds 200]

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import argparse
import json
import os
import sys
import time
import urllib.request

import numpy as np

import replay as rp
import sim

__version__ = "0.1.0"

PLAN_URL = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/PLAN-skew.md"

NOISE = {"none": (0.0, 0.0), "quiet": sim.NOISE["quiet"], "typical": sim.NOISE["typical"],
         "noisy": sim.NOISE["noisy"]}
NOISE_ORDER = ("none", "quiet", "typical", "noisy")
SKEWS = (-2, -1, 0, 1, 2)      # > 0: the current sample is that many samples late; < 0: the voltage sample is
RISES = (0, 25)                # samples over which the arming and takeoff steps ramp (0: a jump; 25: 0.25 s)
VARIANTS = ("px4", "keep")
SEEDS = 200
LOW = 0.5                      # "low": published estimate at the end below LOW x R0
AFTER_SPOOL = 100              # R is also read 1 s after the spool-up starts


# ---------------------------------------------------------------- the estimator

class KeepRls(sim.VecRls):
    """sim.VecRls with PX4's reject branch removed: a rejected update leaves the open-circuit
    estimate as it was. A diagnostic for PLAN-skew.md, not a proposed change to PX4."""

    def update(self, v32, i32, active):
        e0 = self.e0.copy()
        acc = sim.VecRls.update(self, v32, i32, active)
        rej = active & ~acc
        self.e0 = np.where(rej, e0, self.e0).astype(self.f, copy=False)
        return acc


ESTIMATORS = {"px4": sim.VecRls, "keep": KeepRls}


def run_est(t_us, volt, cur, n_cells, cls=sim.VecRls):
    """sim.run_estimator() (connected from the first sample, PX4's prior) with a choice of
    estimator class. Returns dict of (N, S) arrays: r_pub, r_raw, norm (float32), accepted (bool)."""
    t = np.asarray(t_us, dtype=np.int64)
    v = np.asarray(volt, dtype=np.float64)
    i = np.asarray(cur, dtype=np.float64)
    if v.ndim == 1:
        v = v[:, None]
        i = i[:, None]
    n, s = v.shape
    if not np.all(np.isfinite(v)) or not np.all(v >= rp.RECOGNITION_V):
        raise ValueError("run_est handles finite, connected samples only")
    est = cls(n_cells, s)
    out = {"r_pub": np.empty((n, s), dtype=np.float32), "r_raw": np.empty((n, s), dtype=np.float32),
           "norm": np.empty((n, s), dtype=np.float32), "accepted": np.zeros((n, s), dtype=bool)}
    v32 = v.astype(np.float32)
    i32 = i.astype(np.float32)
    init_end = int(t[0]) + rp.INIT_US
    for k in range(n):
        if int(t[k]) <= init_end:
            est.reset(v32[k])
        act = np.isfinite(i[k]) & (i[k] > rp.FLT_EPSILON)
        out["accepted"][k] = est.update(v32[k], i32[k], act)
        out["r_pub"][k] = est.r_pub
        out["r_raw"][k] = est.e1 / est.n
        out["norm"][k] = est.norm
    return out


# ---------------------------------------------------------------- the model

def ramp(cur, rise):
    """The arming (idle to spool) and takeoff (spool to flight) steps spread linearly over `rise`
    samples, ending on the original trajectory. rise 0 leaves the jumps as they are."""
    out = np.array(cur, dtype=np.float64, copy=True)
    if rise <= 0:
        return out
    n_g, n_s, _ = sim.steps()
    for k0, before in ((n_g, sim.I_IDLE), (n_g + n_s, sim.I_SPOOL)):
        for j in range(rise):
            w = (j + 1) / float(rise + 1)
            out[k0 + j] = before + w * (out[k0 + j] - before)
    return out


def cell_data(rng, s, noise, rise):
    """(t_us, true current, true voltage, voltage noise, current noise) for s runs, no ground events.
    The same draws serve every offset and variant of a (noise, rise) pair."""
    sv, si = NOISE[noise]
    cur = ramp(sim.true_current(rng, s, 0), rise)
    volt = sim.true_voltage(cur)
    ev = rng.normal(0.0, sv, size=volt.shape) if sv > 0 else np.zeros(volt.shape)
    ei = rng.normal(0.0, si, size=cur.shape) if si > 0 else np.zeros(cur.shape)
    t = np.arange(len(cur), dtype=np.int64) * sim.DT_US
    return t, cur, volt, ev, ei


def measured(volt, cur, ev, ei, skew):
    """Measured (voltage, current): the current sample `skew` samples late (skew > 0) or the voltage
    sample -skew samples late (skew < 0), plus noise, quantised as in sim.py."""
    v = sim.shift(volt, -skew) if skew < 0 else np.asarray(volt, dtype=np.float64)
    i = sim.shift(cur, skew) if skew > 0 else np.asarray(cur, dtype=np.float64)
    return sim.quant(v + ev, sim.Q_V), sim.quant(i + ei, sim.Q_I)


# ---------------------------------------------------------------- outcomes

def cell_stats(est, takeoff, n_ground):
    r = est["r_pub"].astype(np.float64)
    o = sim.outcomes(r, takeoff)
    r_end = o["r_end"]
    after = r[n_ground + AFTER_SPOOL]
    before = r[n_ground - 1]
    acc_air = est["accepted"][takeoff:].mean(axis=0)
    return {
        "n": int(r.shape[1]),
        "low_end": int(np.sum(r_end < LOW * sim.R0)),
        "in_flight": int(np.sum(o["in_flight"])),
        "at_takeoff": int(np.sum(o["at_takeoff"])),
        "r_end_median": float(np.median(r_end)),
        "r_end_mohm_quantiles": sim._quantiles([float(x) * 1000.0 for x in r_end]),
        "r_before_spool_mohm_quantiles": sim._quantiles([float(x) * 1000.0 for x in before]),
        "r_after_spool_mohm_quantiles": sim._quantiles([float(x) * 1000.0 for x in after]),
        "air_accept_share_quantiles": sim._quantiles([float(x) for x in acc_air]),
    }


def run_pair(noise, rise, seeds=SEEDS):
    """Every offset and variant on one (noise, rise) pair's data. Returns {(variant, skew): stats}."""
    rng = np.random.default_rng([2026, 11, 12, NOISE_ORDER.index(noise), RISES.index(rise)])
    t, cur, volt, ev, ei = cell_data(rng, seeds, noise, rise)
    n_g, n_s, _ = sim.steps()
    takeoff = n_g + n_s
    res = {}
    for skew in SKEWS:
        vm, im = measured(volt, cur, ev, ei, skew)
        for v in VARIANTS:
            res[(v, skew)] = cell_stats(run_est(t, vm, im, sim.N_CELLS, ESTIMATORS[v]), takeoff, n_g)
    return res


# ---------------------------------------------------------------- grading

def _pool(cells, variant, key, skews, rise=0):
    k = n = 0
    for (v, nz, ri, sk), st in cells.items():
        if v == variant and ri == rise and sk in skews:
            k += st[key]
            n += st["n"]
    return k, n


def _share(k, n):
    return round(k / float(n), 4) if n else None


def grade(cells):
    """(predictions, report) with PLAN-skew.md's rules. cells: {(variant, noise, rise, skew): stats}."""
    k0, n0 = _pool(cells, "px4", "low_end", (0,))
    k1 = (10 * k0 <= n0) if n0 else None
    kp, npl = _pool(cells, "px4", "low_end", (1,))
    k2 = (2 * kp >= npl) if npl else None
    km, nm = _pool(cells, "px4", "low_end", (-1,))
    k3 = (2 * km >= nm) if nm else None
    a, na = _pool(cells, "px4", "low_end", (1, 2))
    b, nb = _pool(cells, "keep", "low_end", (1, 2))
    k4 = (2 * b * na <= a * nb) if (na and nb and 5 * a >= na) else None
    c, nc = _pool(cells, "px4", "low_end", (-1, -2))
    d, nd = _pool(cells, "keep", "low_end", (-1, -2))
    k5 = (2 * d * nc >= c * nd) if (nc and nd and 5 * c >= nc) else None

    def med(sk):
        st = cells.get(("px4", "none", 0, sk))
        return st["r_end_median"] if st else None
    m_p, m_m, m_0 = med(1), med(-1), med(0)
    if None in (m_p, m_m, m_0):
        k6 = None
    else:
        k6 = bool(m_p < LOW * sim.R0 and m_m < LOW * sim.R0 and 0.5 * sim.R0 <= m_0 <= 1.5 * sim.R0)
    e, ne = _pool(cells, "px4", "low_end", (-1, 1), rise=0)
    f, nf = _pool(cells, "px4", "low_end", (-1, 1), rise=RISES[1])
    k7 = (2 * f * ne <= e * nf) if (ne and nf and 5 * e >= ne) else None
    pred = {"K1": k1, "K2": k2, "K3": k3, "K4": k4, "K5": k5, "K6": k6, "K7": k7}

    def mohm(x):
        return round(x * 1000.0, 4) if x is not None else None
    rep = {
        "k1_aligned_low": [k0, n0, _share(k0, n0)],
        "k2_current_late_1_low": [kp, npl, _share(kp, npl)],
        "k3_voltage_late_1_low": [km, nm, _share(km, nm)],
        "k4_current_late_low": {"px4": [a, na, _share(a, na)], "keep": [b, nb, _share(b, nb)]},
        "k5_voltage_late_low": {"px4": [c, nc, _share(c, nc)], "keep": [d, nd, _share(d, nd)]},
        "k6_noise_free_median_r_end_mohm": {"skew+1": mohm(m_p), "skew-1": mohm(m_m), "skew0": mohm(m_0)},
        "k7_offset_1_low": {"jump": [e, ne, _share(e, ne)], "ramp": [f, nf, _share(f, nf)]},
    }
    return pred, rep


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--plan", default="PLAN-skew.md")
    ap.add_argument("--wait-min", type=float, default=30)
    ap.add_argument("--seeds", type=int, default=SEEDS)
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)

    def get_plan():
        req = urllib.request.Request(PLAN_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    if not sim.wait_for_plan(args.plan, get_plan, args.wait_min * 60):
        with open(os.path.join(args.out_dir, "skew-summary.json"), "w") as f:
            json.dump({"skew": __version__, "stop": "plan not on main; nothing simulated"}, f, indent=1)
        print("PLAN-skew.md on main is not the local copy: nothing simulated", flush=True)
        return 3
    started = time.time()
    cells = {}
    for noise in NOISE_ORDER:
        for rise in RISES:
            res = run_pair(noise, rise, args.seeds)
            for (v, skew), st in res.items():
                cells[(v, noise, rise, skew)] = st
            print(json.dumps({"noise": noise, "rise": rise,
                              "low_end": {"%s %+d" % (v, sk): res[(v, sk)]["low_end"] for v in VARIANTS
                                          for sk in SKEWS}}), flush=True)
    pred, rep = grade(cells)
    table = []
    for (v, noise, rise, skew), st in cells.items():
        row = {"variant": v, "noise": noise, "rise_samples": rise, "skew_samples": skew}
        row.update(st)
        table.append(row)
    summary = {"skew": __version__, "sim": sim.__version__, "replay": rp.__version__, "seeds": args.seeds,
               "predictions": pred, "reported": rep, "wall_s": round(time.time() - started, 1),
               "model": {"noise": NOISE, "skews": SKEWS, "rises": RISES, "low": LOW, "r0": sim.R0,
                         "after_spool_samples": AFTER_SPOOL}}
    with open(os.path.join(args.out_dir, "skew-summary.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, indent=1) + "\n")
    with open(os.path.join(args.out_dir, "skew-cells.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(table, indent=1) + "\n")
    print(json.dumps(summary, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

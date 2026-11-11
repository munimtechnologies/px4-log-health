"""sim.py: simulate PX4's battery internal-resistance estimator from boot at 100 Hz on a model
battery, to ask whether sensor noise on the ground can drive the estimate to 0 before takeoff
(px4-log-health study battery-r-2026, simulation, pre-registered in
studies/battery-r-2026/PLAN-sim.md).

The estimator is replay.py 0.1.0's port of PX4's Battery::updateInternalResistanceEstimation()
(float32, LAMBDA 0.95, covariance-norm gate), run here vectorised over seeds. A test requires the
vectorised run to equal replay.replay() exactly before the simulation runs.

    python sim.py --out-dir out [--plan PLAN-sim.md] [--wait-min 120] [--seeds 200]

Needs numpy and replay.py 0.1.0 (px4-log-health, studies/battery-r-2026) next to it.
By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import argparse
import json
import math
import os
import statistics
import sys
import time
import urllib.request

import numpy as np

import replay as rp

__version__ = "0.1.0"

PLAN_URL = "https://raw.githubusercontent.com/munimtechnologies/px4-log-health/main/studies/battery-r-2026/PLAN-sim.md"

RATE_HZ = 100
DT_US = 1000000 // RATE_HZ

# model battery (PLAN-sim.md, "The model")
N_CELLS = 4
R0 = 0.0027             # Ohm per cell
R1 = 0.0010             # Ohm per cell, RC pair
TAU_S = 20.0
OCV_FULL = 4.10         # V per cell at the start
OCV_SLOPE = 0.60        # V per cell over the whole capacity
CAPACITY_AH = 5.0

# timeline
GROUND_S = 60.0
SPOOL_S = 3.0
FLIGHT_S = 120.0
I_IDLE = 0.8
I_SPOOL = 3.0
I_HOVER = 15.0
I_SWING = 4.0
SWING_HZ = 0.2
LOAD_SD = 1.0
LOAD_RHO = 0.95

# ground events
EVENTS = 5
EVENT_A = 1.5
EVENT_S = 0.3
EVENT_WINDOW = (5.0, 55.0)

# sensors
Q_V = 0.0145
Q_I = 0.029
NOISE = {"quiet": (0.005, 0.02), "typical": (0.015, 0.05), "noisy": (0.040, 0.15)}
NOISE_ORDER = ("quiet", "typical", "noisy")
SKEWS = (0, 2)
EVENT_SETTINGS = (0, 1)
VARIANTS = ("px4", "f1", "f2")
SMALL_R_COV = 0.001
SEEDS = 200

FLEET = {"source": "GRADE.md: 10 of 75 measurable public flights collapsed", "collapsed": 10, "n": 75}


def steps():
    """(ground, spool, flight) sample counts."""
    return (int(round(GROUND_S * RATE_HZ)), int(round(SPOOL_S * RATE_HZ)), int(round(FLIGHT_S * RATE_HZ)))


# ---------------------------------------------------------------- the estimator, vectorised

class VecRls(object):
    """replay.Rls (gated, float32) for `size` independent runs at once. r_cov: the prior
    covariance on R per cell (None: PX4's, replay.R_COVARIANCE)."""

    def __init__(self, n_cells, size, gated=True, r_cov=None):
        f = np.float32
        self.f = f
        self.gated = gated
        self.n = f(n_cells)
        self.lam = f(rp.LAMBDA)
        self.r_cov = f(rp.R_COVARIANCE if r_cov is None else r_cov)
        self.e0 = np.full(size, f(rp.OCV_DEFAULT) * self.n, dtype=f)
        self.e1 = np.full(size, f(rp.R_DEFAULT) * self.n, dtype=f)
        self.p00_0 = f(rp.OCV_COVARIANCE) * self.n
        self.p11_0 = self.r_cov * self.n
        self.zero = f(0.0)
        self.norm_0 = self._norm(self.p00_0, self.zero, self.p11_0)
        self.p00 = np.full(size, self.p00_0, dtype=f)
        self.p01 = np.full(size, self.zero, dtype=f)
        self.p10 = np.full(size, self.zero, dtype=f)
        self.p11 = np.full(size, self.p11_0, dtype=f)
        self.norm = np.full(size, self.norm_0, dtype=f)
        self.r_pub = np.full(size, f(rp.R_DEFAULT), dtype=f)

    def _norm(self, a, c, d):
        two = self.f(2.0)
        return np.sqrt(a * a + two * c * c + d * d)

    def reset(self, v32):
        """Battery::resetInternalResistanceEstimation() on every run (the reset window is the same
        time for all runs)."""
        f = self.f
        self.e0 = np.array(v32, dtype=f, copy=True)
        self.e1 = np.full_like(self.e1, f(rp.R_DEFAULT) * self.n)
        self.p00 = np.full_like(self.p00, self.p00_0)
        self.p01 = np.full_like(self.p01, self.zero)
        self.p10 = np.full_like(self.p10, self.zero)
        self.p11 = np.full_like(self.p11, self.p11_0)
        self.norm = np.full_like(self.norm, self.norm_0)
        self.r_pub = np.full_like(self.r_pub, f(rp.R_DEFAULT))

    def update(self, v32, i32, active):
        """Battery::updateInternalResistanceEstimation() where `active`; returns accepted (bool)."""
        f = self.f
        with np.errstate(all="ignore"):
            x0 = f(1.0)
            x1 = -i32
            vp = x0 * self.e0 + x1 * self.e1
            pe = v32 - vp
            px0 = self.p00 * x0 + self.p01 * x1
            px1 = self.p10 * x0 + self.p11 * x1
            den = self.lam + (x0 * px0 + x1 * px1)
            g0 = px0 / den
            g1 = px1 / den
            t0 = self.e0 + g0 * pe
            t1 = self.e1 + g1 * pe
            xp0 = x0 * self.p00 + x1 * self.p10
            xp1 = x0 * self.p01 + x1 * self.p11
            q00 = (self.p00 - g0 * xp0) / self.lam
            q01 = (self.p01 - g0 * xp1) / self.lam
            q10 = (self.p10 - g1 * xp0) / self.lam
            q11 = (self.p11 - g1 * xp1) / self.lam
            qn = self._norm(q00, q10, q11)
            if self.gated:
                acc = active & (qn < self.norm)
            else:
                acc = active.copy()
            rej = active & ~acc
            e0_rej = v32 + self.e1 * i32
            self.e0 = np.where(acc, t0, np.where(rej, e0_rej, self.e0)).astype(f, copy=False)
            self.e1 = np.where(acc, t1, self.e1).astype(f, copy=False)
            self.p00 = np.where(acc, q00, self.p00).astype(f, copy=False)
            self.p01 = np.where(acc, q01, self.p01).astype(f, copy=False)
            self.p10 = np.where(acc, q10, self.p10).astype(f, copy=False)
            self.p11 = np.where(acc, q11, self.p11).astype(f, copy=False)
            self.norm = np.where(acc, qn, self.norm).astype(f, copy=False)
            pub = np.maximum(self.e1 / self.n, self.zero)
            self.r_pub = np.where(acc, pub, self.r_pub).astype(f, copy=False)
        return acc


def run_estimator(t_us, volt, cur, n_cells, gated=True, r_cov=None, start=0):
    """replay.replay() for always-connected samples, vectorised: volt and cur are (N, S) or (N,).
    The battery counts as connected from sample `start` (earlier samples keep the initial state:
    published R_DEFAULT, no reset, no update). Returns dict of (N, S) arrays: r_pub, r_raw, norm
    (float32) and accepted (bool)."""
    t = np.asarray(t_us, dtype=np.int64)
    v = np.asarray(volt, dtype=np.float64)
    i = np.asarray(cur, dtype=np.float64)
    if v.ndim == 1:
        v = v[:, None]
        i = i[:, None]
    n, s = v.shape
    est = VecRls(n_cells, s, gated, r_cov)
    out = {"r_pub": np.empty((n, s), dtype=np.float32), "r_raw": np.empty((n, s), dtype=np.float32),
           "norm": np.empty((n, s), dtype=np.float32), "accepted": np.zeros((n, s), dtype=bool)}
    for k in range(min(start, n)):
        out["r_pub"][k] = est.r_pub
        out["r_raw"][k] = est.e1 / est.n
        out["norm"][k] = est.norm
    if start >= n:
        return out
    if not np.all(np.isfinite(v[start:])) or not np.all(v[start:] >= rp.RECOGNITION_V):
        raise ValueError("run_estimator handles finite, connected samples only")
    v32 = v.astype(np.float32)
    i32 = i.astype(np.float32)
    init_end = int(t[start]) + rp.INIT_US
    for k in range(start, n):
        if int(t[k]) <= init_end:
            est.reset(v32[k])
        act = np.isfinite(i[k]) & (i[k] > rp.FLT_EPSILON)
        out["accepted"][k] = est.update(v32[k], i32[k], act)
        out["r_pub"][k] = est.r_pub
        out["r_raw"][k] = est.e1 / est.n
        out["norm"][k] = est.norm
    return out


# ---------------------------------------------------------------- the model

def quant(x, q):
    return np.round(np.asarray(x, dtype=np.float64) / q) * q


def ar1(rng, n, s, rho):
    e = rng.standard_normal((n, s))
    x = np.empty((n, s))
    x[0] = e[0]
    c = math.sqrt(1.0 - rho * rho)
    for k in range(1, n):
        x[k] = rho * x[k - 1] + c * e[k]
    return x


def true_voltage(cur):
    """Pack voltage of the model battery for a true current array (N, S), from full charge."""
    cur = np.asarray(cur, dtype=np.float64)
    if cur.ndim == 1:
        cur = cur[:, None]
    dt = 1.0 / RATE_HZ
    ah = np.zeros_like(cur)
    ah[1:] = np.cumsum(cur[:-1], axis=0) * dt / 3600.0
    ocv = OCV_FULL - OCV_SLOPE * ah / CAPACITY_AH
    vrc = np.zeros_like(cur)
    a = dt / TAU_S
    for k in range(1, len(cur)):
        vrc[k] = vrc[k - 1] + a * (R1 * cur[k - 1] - vrc[k - 1])
    return N_CELLS * (ocv - R0 * cur - vrc)


def shift(x, d):
    """x delayed by d samples (the first d samples repeat x[0])."""
    x = np.asarray(x, dtype=np.float64)
    if d <= 0:
        return x.copy()
    out = np.empty_like(x)
    out[d:] = x[:-d]
    out[:d] = x[0]
    return out


def true_current(rng, s, events):
    n_g, n_s, n_f = steps()
    cur = np.empty((n_g + n_s + n_f, s))
    cur[:n_g] = I_IDLE
    if events:
        starts = rng.uniform(EVENT_WINDOW[0], EVENT_WINDOW[1], size=(EVENTS, s))
        w = int(round(EVENT_S * RATE_HZ))
        for e in range(EVENTS):
            for j in range(s):
                k = int(starts[e, j] * RATE_HZ)
                cur[k:k + w, j] += EVENT_A
    cur[n_g:n_g + n_s] = I_SPOOL
    tf = np.arange(n_f) / float(RATE_HZ)
    phase = rng.uniform(0.0, 2.0 * math.pi, size=s)
    load = ar1(rng, n_f, s, LOAD_RHO) * LOAD_SD
    cur[n_g + n_s:] = I_HOVER + I_SWING * np.sin(2.0 * math.pi * SWING_HZ * tf[:, None] + phase[None, :]) + load
    return cur


def inputs(rng, s, noise, events, skew):
    """(t_us, true current, true voltage, measured voltage, measured current) for s runs."""
    sv, si = NOISE[noise]
    cur = true_current(rng, s, events)
    volt = true_voltage(cur)
    vm = quant(volt + rng.normal(0.0, sv, size=volt.shape), Q_V)
    im = quant(shift(cur, skew) + rng.normal(0.0, si, size=cur.shape), Q_I)
    t = np.arange(len(cur), dtype=np.int64) * DT_US
    return t, cur, volt, vm, im


# ---------------------------------------------------------------- outcomes

def outcomes(r_pub, takeoff):
    """Per run: collapsed at takeoff (published 0 at the last sample before takeoff), collapsed in
    flight (0 for the whole second half of the flight), R_end."""
    r = np.asarray(r_pub, dtype=np.float64)
    fl = r[takeoff:]
    half = fl[len(fl) // 2:]
    return {"at_takeoff": r[takeoff - 1] <= 0.0, "in_flight": np.all(half <= 0.0, axis=0), "r_end": fl[-1]}


def cell_stats(est, takeoff, n_ground_start=0):
    o = outcomes(est["r_pub"], takeoff)
    r = est["r_pub"].astype(np.float64)
    ground = r[n_ground_start:takeoff]
    touched = np.any(ground <= 0.0, axis=0) if len(ground) else np.zeros(r.shape[1], dtype=bool)
    first = []
    for j in np.flatnonzero(touched):
        first.append(round((n_ground_start + int(np.argmax(ground[:, j] <= 0.0))) / float(RATE_HZ), 2))
    nf = o["in_flight"]
    rel = np.abs(o["r_end"][~nf] / R0 - 1.0)
    acc_air = est["accepted"][takeoff:].mean(axis=0)
    return {
        "n": int(r.shape[1]),
        "at_takeoff": int(np.sum(o["at_takeoff"])),
        "in_flight": int(np.sum(nf)),
        "both": int(np.sum(o["at_takeoff"] & nf)),
        "touched_zero_on_ground": int(np.sum(touched)),
        "first_zero_s_quantiles": _quantiles(first),
        "rel_err": [float(x) for x in rel],
        "rel_err_quantiles": _quantiles([float(x) for x in rel]),
        "r_end_mohm_quantiles": _quantiles([float(x) * 1000.0 for x in o["r_end"]]),
        "norm_at_takeoff_quantiles": _quantiles([float(x) for x in est["norm"][takeoff - 1]]),
        "air_accept_share_quantiles": _quantiles([float(x) for x in acc_air]),
    }


def _quantiles(xs):
    xs = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not xs:
        return None

    def q(p):
        return round(xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))], 6)
    return {"n": len(xs), "p10": q(0.10), "p25": q(0.25), "median": round(statistics.median(xs), 6),
            "p75": q(0.75), "p90": q(0.90), "min": round(xs[0], 6), "max": round(xs[-1], 6)}


def run_cell(noise, events, skew, seeds=SEEDS):
    """All three variants on one cell's simulated data. Returns {variant: stats}."""
    rng = np.random.default_rng([2026, 11, NOISE_ORDER.index(noise), events, skew])
    t, cur, volt, vm, im = inputs(rng, seeds, noise, events, skew)
    n_g, n_s, _ = steps()
    takeoff = n_g + n_s
    res = {}
    res["px4"] = cell_stats(run_estimator(t, vm, im, N_CELLS), takeoff)
    res["f1"] = cell_stats(run_estimator(t, vm, im, N_CELLS, r_cov=SMALL_R_COV), takeoff)
    res["f2"] = cell_stats(run_estimator(t, vm, im, N_CELLS, start=n_g), takeoff, n_ground_start=n_g)
    return res


# ---------------------------------------------------------------- grading

def _pool(cells, variant, key, noise=None):
    k = n = 0
    for (v, nz, ev, sk), st in cells.items():
        if v != variant or (noise is not None and nz != noise):
            continue
        k += st[key]
        n += st["n"]
    return k, n


def _share(k, n):
    return round(k / float(n), 4) if n else None


def grade(cells):
    """(predictions, report) with PLAN-sim.md's rules. cells: {(variant, noise, events, skew): stats}."""
    q = cells.get(("px4", "quiet", 0, 0))
    h1 = (20 * q["at_takeoff"] <= q["n"]) if q else None
    kn, nn = _pool(cells, "px4", "at_takeoff", "noisy")
    kq, nq = _pool(cells, "px4", "at_takeoff", "quiet")
    diff = round(kn / float(nn) - kq / float(nq), 9) if nn and nq else None
    h2 = (diff >= 0.10) if diff is not None else None
    kb, _ = _pool(cells, "px4", "both")
    kt, nt = _pool(cells, "px4", "at_takeoff")
    h3 = (10 * kb >= 7 * kt) if kt >= 30 else None
    k1, n1 = _pool(cells, "f1", "at_takeoff")
    h4 = (2 * k1 * nt <= kt * n1) if (nt and n1 and 20 * kt >= nt) else None
    kf, nf = _pool(cells, "px4", "in_flight")
    k2, n2 = _pool(cells, "f2", "in_flight")
    h5 = (2 * k2 * nf <= kf * n2) if (nf and n2 and 20 * kf >= nf) else None
    rel = [x for (v, _, _, _), st in cells.items() if v == "px4" for x in st["rel_err"]]
    med = statistics.median(rel) if rel else None
    h6 = (med <= 0.5) if len(rel) >= 100 else None
    pred = {"H1": h1, "H2": h2, "H3": h3, "H4": h4, "H5": h5, "H6": h6}
    rep = {
        "h1_quiet_cell": {"at_takeoff": q["at_takeoff"], "n": q["n"]} if q else None,
        "h2_noisy": [kn, nn, _share(kn, nn)], "h2_quiet": [kq, nq, _share(kq, nq)], "h2_diff": diff,
        "h3_both": kb, "h3_at_takeoff": kt, "h3_share": _share(kb, kt),
        "px4_at_takeoff": [kt, nt, _share(kt, nt)], "f1_at_takeoff": [k1, n1, _share(k1, n1)],
        "px4_in_flight": [kf, nf, _share(kf, nf)], "f2_in_flight": [k2, n2, _share(k2, n2)],
        "h6_runs": len(rel), "h6_median_rel_err": round(med, 4) if med is not None else None,
        "fleet": FLEET,
    }
    return pred, rep


# ---------------------------------------------------------------- the plan gate

def normalised(data):
    return data.replace(b"\r\n", b"\n")


def wait_for_plan(local_path, get_fn, timeout_s, sleep=time.sleep, clock=time.time, every_s=300):
    """True once get_fn() returns bytes equal to the local plan (line endings normalised)."""
    with open(local_path, "rb") as f:
        mine = normalised(f.read())
    t0 = clock()
    while True:
        try:
            if normalised(get_fn()) == mine:
                return True
            print("the plan on main differs from the local copy", flush=True)
        except Exception as exc:
            print("plan not readable yet: %s" % str(exc)[:120], flush=True)
        if clock() - t0 >= timeout_s:
            return False
        sleep(every_s)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--plan", default="PLAN-sim.md")
    ap.add_argument("--wait-min", type=float, default=120)
    ap.add_argument("--seeds", type=int, default=SEEDS)
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)

    def get_plan():
        req = urllib.request.Request(PLAN_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    if not wait_for_plan(args.plan, get_plan, args.wait_min * 60):
        with open(os.path.join(args.out_dir, "sim-summary.json"), "w") as f:
            json.dump({"sim": __version__, "stop": "plan not on main; nothing simulated"}, f, indent=1)
        print("PLAN-sim.md on main is not the local copy: nothing simulated", flush=True)
        return 3
    started = time.time()
    cells = {}
    for noise in NOISE_ORDER:
        for events in EVENT_SETTINGS:
            for skew in SKEWS:
                res = run_cell(noise, events, skew, args.seeds)
                for v in VARIANTS:
                    cells[(v, noise, events, skew)] = res[v]
                print(json.dumps({"noise": noise, "events": events, "skew": skew,
                                  "at_takeoff": {v: res[v]["at_takeoff"] for v in VARIANTS},
                                  "in_flight": {v: res[v]["in_flight"] for v in VARIANTS}}), flush=True)
    pred, rep = grade(cells)
    table = []
    for (v, noise, events, skew), st in cells.items():
        row = {"variant": v, "noise": noise, "events": events, "skew_samples": skew}
        row.update({k: st[k] for k in st if k != "rel_err"})
        table.append(row)
    summary = {"sim": __version__, "replay": rp.__version__, "seeds": args.seeds, "predictions": pred,
               "reported": rep, "wall_s": round(time.time() - started, 1),
               "model": {"n_cells": N_CELLS, "r0": R0, "r1": R1, "tau_s": TAU_S, "ground_s": GROUND_S,
                         "spool_s": SPOOL_S, "flight_s": FLIGHT_S, "noise": NOISE, "q_v": Q_V, "q_i": Q_I,
                         "events": EVENTS, "event_a": EVENT_A, "small_r_cov": SMALL_R_COV}}
    with open(os.path.join(args.out_dir, "sim-summary.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, indent=1) + "\n")
    with open(os.path.join(args.out_dir, "sim-cells.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(table, indent=1) + "\n")
    print(json.dumps(summary, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

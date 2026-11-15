# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name.
"""gate_props 0.1.0: a small, dependency-free model of PX4's battery internal-resistance RLS update.

It follows Battery::updateInternalResistanceEstimation() and Battery::resetInternalResistanceEstimation() in
PX4-Autopilot src/lib/battery/battery.cpp (revision 6ea3539157ca358c70a515878b77077af7d4611d; the function body on
main was the same when checked on 2026-11-15). PX4 computes in float32; this model uses Python floats (float64).
The properties tested in test_gate_props.py are structural, so they hold at either precision:

  P1  Whether an update is accepted depends only on the current samples (and the state at the last reset),
      never on the measured voltage.
  P2  The covariance norm is a running minimum between resets: it never increases.
  P3  A rejected sample re-anchors the OCV state so that the model reproduces that sample's voltage exactly.
  P4  While samples are rejected, the resistance state (signed) and the covariance do not change at all.
  P5  If the current stays constant, the gate rejects after a bounded number of accepted updates and then
      rejects every later sample at that current, whatever the voltage does.

PX4 code is BSD-3-Clause; this re-implementation is original and Apache-2.0.
"""

import math

__version__ = "0.1.0"

# Constants from PX4 src/lib/battery/battery.h
LAMBDA = 0.95          # RLS forgetting factor
R_DEFAULT = 0.005      # [Ohm] initial per-cell internal resistance
OCV_DEFAULT = 4.2      # [V] initial per-cell open-circuit voltage
R_COVARIANCE = 0.1     # initial per-cell covariance of R
OCV_COVARIANCE = 1.5   # initial per-cell covariance of OCV
FLT_EPSILON = 1.1920928955078125e-07


def cov_norm(p00, p10, p11):
    """PX4's covariance norm: sqrt(P00^2 + 2*P10^2 + P11^2) (the Frobenius norm of a symmetric 2x2 matrix)."""
    return math.sqrt(p00 * p00 + 2.0 * p10 * p10 + p11 * p11)


class Rls:
    """State of PX4's estimator: e0 = pack OCV [V], e1 = pack resistance [Ohm] (signed), P (2x2), norm."""

    def __init__(self, n_cells, voltage_v=None, current_a=0.0):
        if n_cells <= 0:
            raise ValueError("n_cells must be positive")
        self.n = n_cells
        self.reset(voltage_v, current_a)

    def reset(self, voltage_v=None, current_a=0.0):
        """resetInternalResistanceEstimation(); with voltage_v None, the updateParams() seeding instead."""
        n = self.n
        self.e0 = OCV_DEFAULT * n if voltage_v is None else float(voltage_v)
        self.e1 = R_DEFAULT * n
        self.p00 = OCV_COVARIANCE * n
        self.p01 = 0.0
        self.p10 = 0.0
        self.p11 = R_COVARIANCE * n
        self.norm = cov_norm(self.p00, self.p10, self.p11)
        self.r_pub = R_DEFAULT

    def predict(self, current_a):
        """Voltage the model expects at this current: x^T * est with x = [1, -I]."""
        return self.e0 - current_a * self.e1

    def step(self, voltage_v, current_a):
        """One call as made from calculateStateOfChargeVoltageBased().

        Returns None when PX4 would not call the update (current <= FLT_EPSILON), otherwise a dict with
        accepted, prediction, error and the candidate norm.
        """
        if not current_a > FLT_EPSILON:
            return None
        x0 = 1.0
        x1 = -float(current_a)
        pred = x0 * self.e0 + x1 * self.e1
        err = voltage_v - pred
        # P * x
        px0 = self.p00 * x0 + self.p01 * x1
        px1 = self.p10 * x0 + self.p11 * x1
        # x^T * P
        xp0 = x0 * self.p00 + x1 * self.p10
        xp1 = x0 * self.p01 + x1 * self.p11
        den = LAMBDA + (x0 * px0 + x1 * px1)
        g0 = px0 / den
        g1 = px1 / den
        c0 = self.e0 + g0 * err
        c1 = self.e1 + g1 * err
        q00 = (self.p00 - g0 * xp0) / LAMBDA
        q01 = (self.p01 - g0 * xp1) / LAMBDA
        q10 = (self.p10 - g1 * xp0) / LAMBDA
        q11 = (self.p11 - g1 * xp1) / LAMBDA
        qn = cov_norm(q00, q10, q11)
        accepted = qn < self.norm
        if accepted:
            self.e0, self.e1 = c0, c1
            self.p00, self.p01, self.p10, self.p11 = q00, q01, q10, q11
            self.norm = qn
            self.r_pub = max(self.e1 / self.n, 0.0)
        else:
            self.e0 = voltage_v + self.e1 * current_a
        return {"accepted": accepted, "prediction": pred, "error": err, "candidate_norm": qn}


def run(volts, amps, n_cells=4, voltage0=None):
    """Feed paired samples through a fresh estimator; returns (estimator, list of step results)."""
    if len(volts) != len(amps):
        raise ValueError("volts and amps differ in length")
    est = Rls(n_cells, voltage0)
    out = []
    for v, i in zip(volts, amps):
        out.append(est.step(v, i))
    return est, out


def implied_pack_r(v_prev, i_prev, pred_next, i_next):
    """Signed pack R implied by two consecutive samples when the first was rejected (P3/P4).

    After a rejected sample, e0 = v_prev + R*i_prev, so the next prediction is v_prev + R*(i_prev - i_next).
    Solving for R gives the stored signed resistance from logged values only.
    """
    di = i_prev - i_next
    if di == 0:
        raise ValueError("currents must differ")
    return (pred_next - v_prev) / di

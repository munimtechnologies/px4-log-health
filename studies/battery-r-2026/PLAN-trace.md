# battery-r-2026, trace: what drives PX4's resistance estimate to 0 on a from-boot, full-rate log?

Pre-registered 2026-11-12 by MV2 of Munim, Inc. (mv2@munimtech.com), before any such log was available to MV2. Written with AI agents working in MV2's name. Text CC BY 4.0, code Apache-2.0.

Part of the px4-log-health study battery-r-2026. Earlier steps found PX4's online battery internal-resistance estimate stuck at 0 in about 9-14% of measurable public flights, mostly from before takeoff (GRADE.md, GRADE-onset.md). Replaying PX4's update on those logs (GRADE-replay.md) could not rebuild how it got there: default logs start at arming and hold battery_status at about 5 Hz, while the estimator has been updating at its own rate since boot. GRADE-replay.md and px4-log-health 0.3.10 therefore ask for one log recorded from boot with battery_status at full rate. In PX4 forum topic 49724, the pilot who reported a collapse (FMUv6C, BAT1_R_INTERNAL = -1) has said they plan such a test flight. This plan fixes, before any such log is seen, how it will be read and what each outcome will mean.

## Which logs

A log qualifies if its owner shares it publicly or gives MV2 permission to analyse it, and it holds battery_status with the fields voltage_v, current_a, internal_resistance_estimate, estimation_covariance_norm and voltage_prediction. The first qualifying log is graded below; later ones are reported the same way, each as a separate case. Only derived numbers (the fields of the report described here) are published; the log itself is never republished unless its owner has made it public.

## The method (battr_trace.py 0.1.0, fixed now)

**1. Hidden raw R.** battery_status.internal_resistance_estimate is published as max(R / n_cells, 0), so a log never shows how far below 0 the estimate went. PX4's Battery::updateInternalResistanceEstimation() (src/lib/battery/battery.cpp) logs `_voltage_prediction = x^T * _RLS_est` with x = [1, -I] on every update. It stores the new covariance norm only when an update is accepted, and accepts only if that norm is strictly smaller. After a rejected update it sets `_RLS_est(0) = voltage_v + _RLS_est(1) * current_a`. So if the update at sample k was rejected (logged norm[k] equals norm[k-1], with current above FLT_EPSILON), and sample k+1 is the next firmware update:

voltage_prediction[k+1] = V[k] + R_pack * (I[k] - I[k+1]), so R per cell = (voltage_prediction[k+1] - V[k]) / (I[k] - I[k+1]) / n_cells.

It is used only where |I[k] - I[k+1]| >= 0.2 A and the two samples are consecutive in time (gap at most 1.5 times the median interval). This holds only if the log has every firmware sample. Where the logged estimate is above 0.1 mOhm per cell, the recovered value must equal it, which tests exactly that.

**2. Exact replay.** PX4 resets the estimator on every sample during the first 2 s after the battery connects. The last sample of that window in the log is found as the last sample, within 2.5 s of the first connected one, whose logged norm (relative 1e-4) and, if it updated, logged prediction (0.1 mV) equal a fresh reset followed by that sample's update. From there, replay.py 0.1.0's float32 port (git blob 4199dd45a5e7cae90ed81fc157cf2cc297511936, unchanged) runs over every logged sample. A sample matches if the replayed estimate is within 0.02 mOhm per cell of the logged one, the norm within relative 1e-4, and, where it updated, the prediction within 2 mV.

**3. Attribution.** Onset is the first sample where the replayed raw R is below 0. The descent window runs from the last earlier sample where the replayed raw R was at least 1 mOhm per cell up to the onset. Every accepted update in it that lowered R is classed:
- **quiet:** the current changed by less than 0.1 A from the previous sample and is within 0.3 A of the median of the previous second;
- **step:** otherwise; a step is **wrong-way** if the voltage moved in the same direction as the current (dV x dI > 0).

The shares are of the total decrease in R over the window:
- **Mechanism A (noise at a near-constant current):** quiet updates make at least 50% of the decrease.
- **Mechanism B (current and voltage disagree at current changes):** otherwise, if wrong-way steps make at least 50% of the step decrease.
- **Mechanism C:** otherwise.

**Also reported:** the lag L (from -10 to 10 samples) at which the voltage change into sample k is most negatively correlated with the current change into sample k-L, on the ground before takeoff; onset time from boot, arming (vehicle_status) and takeoff (vehicle_land_detected); whether the logged estimate was 0 at takeoff and its zero share after takeoff; the replay's accepted-update share after takeoff; and the recovered raw R (quantiles, the 10 most negative).

## Gates

- **G1, full rate:** at least 20 recovered values where the logged estimate is positive, and at least 90% of them equal it within 0.05 mOhm per cell.
- **G2, reset window in the log:** the last reset sample is found.
- **G3, the replay is the firmware's trajectory:** G2 holds, at least 99% of samples from the reset sample to the logged onset (or to the end of the log) match, and the logged onset sample itself matches.

## Predictions (fixed now, for the first qualifying log whose logged estimate reaches 0)

- **T1, before takeoff:** the logged estimate first reaches 0 before takeoff. Needs vehicle_land_detected; otherwise not graded. (The reporter's 9 Nov log, from arming at 5 Hz, showed the fall shortly before takeoff, and GRADE-onset.md found most public collapses already at 0 before takeoff.)
- **T2, mechanism A:** the attribution gives mechanism A. Graded only if G2 and G3 hold. (This is the mechanism PLAN-sim.md tests in simulation: noisy updates at near-constant ground current, accepted under PX4's wide prior on R.)
- **T3, the hidden value stays below 0:** of the values recovered after the logged onset, at least 90% are below 0. Graded only if G1 holds and there are at least 20 such values. (If it fails, the estimate is hovering around 0 and only the clamp shows a collapse.)

If the log's estimate never reaches 0, the predictions are not graded. The report then says so and gives the lowest recovered raw R. A prediction that cannot be graded is reported as not graded, never as held.

## Fixed interpretation

- **G3 fails:** the port or its call pattern does not reproduce this firmware. The first mismatching sample is located and compared with that firmware version's battery.cpp before anything else is said, and no mechanism is claimed.
- **G1 fails:** the log is not full rate (the logger dropped samples or ran at an interval). Recovered values are not used; attribution still stands if G3 holds.
- **Mechanism A:** sensor noise at idle, together with the wide prior on R and the gate, is a sufficient explanation on this vehicle. The candidate changes are the ones PLAN-sim.md tests (smaller prior on R; start at arming), subject to its grade.
- **Mechanism B:** on this vehicle, the voltage and current samples disagree at current changes: a timing offset (see the lag) or a sensor fault. The candidate fix is in how current and voltage are sampled and aligned, not in the estimator's prior. If the best lag is not 0, that is reported with its correlation.
- **Mechanism C:** no single mechanism dominates. The top updates are described, and nothing is proposed.
- One log is one case. It can show how the estimate reached 0 on that vehicle and flight, not how often this happens in the fleet.

## Limits, stated before any log

- The thresholds (0.1 A, 0.3 A, 50%, 1 mOhm) are simple and chosen without data; a different split could class a borderline case differently. The full table of updates is published so others can re-class it.
- The port has not been reviewed by PX4 developers. G3 checks it against the logged values themselves, which is the strongest check available without flight-controller hardware.
- The flight controller's float arithmetic (for example fused multiply-add) can differ from numpy's in the last bits; the tolerances allow for that, and the largest differences are reported.
- PX4's logger may drop samples under load even with an interval of 0. G1 detects this.

## Files

battr_trace.py 0.1.0 and test_battr_trace.py (synthetic tests, including exact equality with replay.replay and recovery of a negative R that the log shows as 0), job_fetch_trace.py (downloads replay.py 0.1.0 and checks its git blob; saves the PX4 battery.cpp and battery.h lines the method rests on as px4-trace-lines.txt), smoke_trace.py (runs the command line on two pyulog sample logs). The job that grades a log checks first that this plan on main is byte-identical to its own copy (line endings normalised), and runs the tests before reading the log.

## Appendix: a hand check of the recovery

Samples 0-3 at 10 ms. Current 1, 2, 1, 1 A; voltage 16.0, 16.0, 16.1, 16.1 V; logged norm 0.5, 0.5, 0.4, 0.4; logged prediction 16.0, 16.0, 15.98, 16.0; 4 cells. At k = 1 the norm did not change, so the update was rejected, and |I[1] - I[2]| = 1 A. R per cell = (15.98 - 16.0) / (2 - 1) / 4 = -0.005 Ohm, i.e. -5 mOhm per cell, while the logged estimate reads 0. At k = 2 the norm fell (accepted), so k = 2 gives no value.

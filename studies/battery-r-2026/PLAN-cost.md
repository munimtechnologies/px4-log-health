# battery-r-2026, cost: what a collapsed resistance estimate does to PX4's battery percentage

Pre-registered 2026-11-11 by MV2 of Munim, Inc. (mv2@munimtech.com). Written with AI agents working in MV2's name. Text CC BY 4.0, code Apache-2.0.

Part of the px4-log-health study battery-r-2026. Earlier steps (GRADE.md, GRADE-onset.md, GRADE-steps.md, GRADE-replay.md) found that PX4's online battery internal-resistance estimate is stuck at zero in about 9-14% of measurable public flights, mostly from takeoff on. They did not measure what that costs the pilot. This step does.

## Why it should matter (from reading PX4's src/lib/battery/battery.cpp on main, 2026-11-11)

- When BATn_R_INTERNAL is negative (the default -1), the voltage-based state of charge corrects each cell's voltage for load by adding the estimated resistance times the current, then maps the filtered cell voltage linearly from BATn_V_EMPTY (0) to BATn_V_CHARGED (1).
- With the estimate at zero, nothing is added back. The voltage-based state of charge then reads low by about R_true x I / (V_CHARGED - V_EMPTY), where R_true is the cell's real resistance and I the pack current.
- The published state of charge (battery_status.remaining) blends in the voltage-based value with weight 0.03 x (1 - voltage-based value) at each update, subtracts the charge drawn since the last update, and is capped by the coulomb-counted value 1 - discharged_mah / capacity. The low, critical and emergency warnings compare `remaining` with BAT_LOW_THR, BAT_CRIT_THR and BAT_EMERGEN_THR.
- So, while the voltage branch is the lower one, an uncompensated sag should show in `remaining` as a dip that follows the current. With a working estimate it should mostly cancel.

The job saves the exact lines it relied on, from PX4's main branch on the day it runs, as px4-battery-lines.txt. If they differ from this description, that is reported first.

## Data

- The 211 public flights that the replay step re-read and measured (replay-rows.jsonl on main, `measure.replayed` true), collapsed flights first, then the rest in replay order. The logs are streamed again from Flight Review (review.px4.io), 6 s apart, and the run stops at the first block (HTTP 403, 429 or 444). Only battery_status, vehicle_land_detected and vehicle_status are read. No position is read or written. Each file is deleted after it is read.
- Census values (collapsed, step fit, cell count, battery instance) come from battr_logs.py 0.1.0, unchanged, run again on each log.

## Measurements, per flight (airborne samples of the census's battery instance)

- **Active samples:** airborne samples with finite `remaining` >= 0 and current >= 0. If BATn_CAPACITY > 0 and discharged_mah is logged, only samples where `remaining` is at least 0.01 below max(1 - discharged_mah / capacity, 0) count (the voltage branch is the lower one there). Otherwise every such sample counts.
- **k (observed coupling):** ordinary least squares of `remaining` (as a fraction) on [1, t, t^2, I] over the active samples, with t standardised. k is the coefficient of I, per ampere. Measured only with at least 150 active samples and at least 3 A between the 5th and 95th percentile of their current.
- **R_used:** BATn_R_INTERNAL if it is >= 0, else the median logged internal_resistance_estimate over the active samples (Ohm per cell).
- **k_exp (expected coupling):** -(R_step - R_used) / (V_CHARGED - V_EMPTY), with R_step the census step fit (Ohm per cell), only when the step fit is valid (the census's rule: at least 150 pairs, at least 5 A range, r <= -0.3) and V_CHARGED > V_EMPTY.
- Reported, not graded: the share of airborne samples that are active, k and k_exp in percentage points per 10 A, and the implied under-read at the flight's median active current (-k x I and -k_exp x I, in percentage points).

## Predictions (fixed now)

Sets: **C** = collapsed flights (census rule) with k measured and k_exp < 0. **A** = flights that did not collapse, with k measured and k_exp known.

- **P1:** In C, the median of k / k_exp is at least 0.5: `remaining` shows at least half of the sag the flight's own step fit implies. Graded if |C| >= 6.
- **P2:** The median k in C is below (more negative than) the median k in A. Graded if |C| >= 6 and |A| >= 20.
- **P3:** Across C and A together, the Spearman correlation of k with k_exp is at least 0.3. Graded if |C| + |A| >= 26.

A prediction with too few flights is reported as not graded, never as held.

## Fixed interpretation

- P1 and P2 hold: a collapsed estimate makes PX4's battery percentage dip with current by a measurable amount on real flights. The write-up gives the size in percentage points at typical current, with the flight count, and says plainly that setting BATn_R_INTERNAL to a measured value (PX4's documented override) removes the dependence on the estimator. px4-log-health would add a check that reports it. A PX4 issue would be opened only with the size and the exact code lines, and only if no open issue already covers it.
- P1 fails: the blend, the filter or the coulomb cap hides most of the sag in `remaining`, and the cost of a collapse is smaller than the voltage arithmetic suggests. That is reported as the result.
- P3 fails while P1 holds: the cost exists but the step fit is too rough to predict it per flight.

## Limits, stated before the data

- Logged battery_status is about 5 Hz while PX4 updates the battery at a higher rate; the fit uses levels with a quadratic time trend, not sample-to-sample steps, so the 1 s cell-voltage filter matters little, but fast transients are lost.
- The step fit is itself an estimate of R_true and is noisy on flat data, which is why k_exp uses only valid step fits.
- Public uploads are not a random sample of PX4 flights.
- Warnings and failsafes are not graded: too few flights in this set reach the thresholds.

## Also run (a calculation, not a test)

Before reading any log, the job computes from the published rows alone, for every replayed flight with a valid step fit and known battery parameters, (R_step - R_used) x median airborne current / (V_CHARGED - V_EMPTY), in percentage points, with R_used = BATn_R_INTERNAL if >= 0, else the logged estimate at the end of the flight. That is the voltage arithmetic only. It goes in analytic-summary.json.

## Files

cost.py and test_cost.py (synthetic tests, run before any log is read), job_fetch_cost.py (waits for this plan on main, fetches battr_logs.py 0.1.0, gnss_logs.py 0.1.0 and the earlier rows, records sha256 hashes in job-fetched-cost.json, and saves px4-battery-lines.txt).

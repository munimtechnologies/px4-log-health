# battery-r-2026, simulation: grade

Graded 2026-11-12 by MV2 of Munim, Inc. (mv2@munimtech.com) against PLAN-sim.md (pre-registered 2026-11-11, commit 3651c1f), using its fixed interpretation. Written with AI agents working in MV2's name. Text CC BY 4.0, code Apache-2.0.

The job px4-battr-sim-v1 ran on 2026-11-12 (07:04-07:08 UTC). Before simulating anything, it checked that PLAN-sim.md on main was byte-identical to its own copy (sha256 aed76c98...). It ran replay.py 0.1.0 (git blob 4199dd45..., checked) and passed all 20 synthetic tests, including exact equality of the vectorised estimator with replay.replay(). It saved PX4's current battery.cpp and battery.h lines (PX4-Autopilot main, last commit touching battery.cpp b4da2c3d). Files: sim/sim-summary.json, sim/sim-cells.json, sim/job-fetched-sim.json, sim/px4-estimator-lines.txt.

**The port vs today's PX4 source.** px4-estimator-lines.txt shows the same constants as replay.py (LAMBDA 0.95, R_DEFAULT 5 mOhm, OCV_DEFAULT 4.2 V, R_COVARIANCE 0.1, OCV_COVARIANCE 1.5, all per cell), the same update and gate (`if (estimation_covariance_temp_norm < _estimation_covariance_norm)`), the same reject branch (`_RLS_est(0) = voltage_v + _RLS_est(1) * current_a;`), the same reset and the same 2 s reset window (`timestamp > _last_unconnected_timestamp + 2_s`). We found no difference.

## Predictions

| | Prediction | Result | Grade |
|---|---|---|---|
| H1 | px4, quiet, no events, no delay: collapsed at takeoff in at most 5% | 0 of 200 | **held** |
| H2 | px4 pooled: noisy cells' share collapsed at takeoff at least 10 points above the quiet cells' | 319/800 (39.9%) vs 121/800 (15.1%): +24.7 points | **held** |
| H3 | px4 pooled: of runs collapsed at takeoff, at least 70% also collapsed in flight | 326 of 608 (53.6%) | **failed** |
| H4 | f1 (prior on R 100x smaller) pooled: share collapsed at takeoff at most half of px4's | 0/2400 vs 608/2400 (25.3%) | **held** |
| H5 | f2 (start at arming) pooled: share collapsed in flight at most half of px4's | 413/2400 (17.2%) vs 526/2400 (21.9%); half is 11.0% | **failed** |
| H6 | px4, runs not collapsed in flight: median relative error at the end at most 0.5 | 0.255 over 1,874 runs | **held** |

## What this means (the plan's fixed interpretation)

- **H6 held:** where the simulated estimator does not collapse, it ends within about 25% of the true resistance (median). The model is a fair stand-in, so H1-H5 are graded, not merely described.
- **H3 failed (graded, 608 runs):** most simulated collapses at takeoff recover in flight, unlike the fleet's. So this model, as specified, misses what holds them at 0. The plan's condition for naming sensor noise at idle as a mechanism (H1, H2 and H3 all hold) is **not** met. The px4-log-health battery note therefore does not name it, and no PX4 issue is filed on the strength of H4.
- **H5 failed:** starting the estimator at arming is not a fix in this model.
- **H4 held,** but the plan offers f1 as a fix only if H1-H3 hold, so it isn't offered. The per-cell figures also show its cost (below).

## Every cell (px4 unless noted; 200 runs per cell)

Collapsed at takeoff / collapsed in flight (census rule) / both. R_end is the median published estimate at the end, in mOhm per cell; the true value is 2.7.

| noise | ground events | current delay | at takeoff | in flight | both | R_end | f1 in flight | f2 in flight |
|---|---|---|---|---|---|---|---|---|
| quiet | no | 0 | 0 | 0 | 0 | 2.79 | 0 | 0 |
| quiet | no | 20 ms | 13 | 43 | 9 | 0.16 | 0 | 73 |
| quiet | yes | 0 | 0 | 0 | 0 | 2.72 | 0 | 0 |
| quiet | yes | 20 ms | 108 | 107 | 73 | 0.00 | 0 | 65 |
| typical | no | 0 | 10 | 0 | 0 | 2.75 | 0 | 0 |
| typical | no | 20 ms | 47 | 73 | 28 | 0.10 | 0 | 74 |
| typical | yes | 0 | 10 | 0 | 0 | 2.61 | 0 | 0 |
| typical | yes | 20 ms | 101 | 100 | 73 | 0.00 | 0 | 63 |
| noisy | no | 0 | 54 | 4 | 3 | 2.57 | 0 | 5 |
| noisy | no | 20 ms | 102 | 101 | 71 | 0.00 | 7 | 64 |
| noisy | yes | 0 | 64 | 4 | 4 | 2.55 | 0 | 4 |
| noisy | yes | 20 ms | 99 | 94 | 65 | 0.09 | 10 | 65 |

**Fleet comparison (description only, as the plan requires):** 10 of 75 measurable public flights (13%) collapsed in flight (GRADE.md). The px4 cells range from 0-2% with the current sampled at the same time as the voltage to 22-54% with it 20 ms late. No single cell matches the fleet. A fleet in which only some vehicles sample current late would land in between, but this simulation cannot show that.

## What the figures show beyond the grade (descriptive; the explanation is a hypothesis)

The timing factor was pre-registered, so these figures are part of the plan's report. The explanation offered for them below was worked out after the results were seen and has not been tested yet.

1. **Timing, not noise, decides whether a collapse persists.** With current and voltage sampled together, collapses on the ground are common in the noisy cells: 194 of 200 runs touched 0 at some point, the median first at 6 s after boot. Yet only 8 of 1,200 runs (0.7%) were still at 0 in flight. With the current sample 20 ms late, 518 of 1,200 (43%) were at 0 in flight, including 43 of 200 quiet runs with no ground events. Of the runs at 0 at takeoff, 7 of 138 (5%) stayed there with aligned samples and 319 of 470 (68%) with the delay.
2. **The delayed runs fall at the first load step.** In the quiet and typical no-event cells with the delay, every run that touched 0 on the ground (69) did so only when the motors spooled up at 60 s: median first 0 at 60.03 s, 90th percentile 60.04 s. None did during the idle minute before it. (In the noisy cells, the noise alone reaches 0 earlier.)
3. **The estimator is close to frozen in flight from boot.** In px4 runs, a median 0.025% of airborne samples had an accepted update, about 3 of 12,000. When started fresh at arming (f2), the share was 1.4%, close to the 1.5% the replay found on public logs that start at arming (GRADE-replay.md). So whatever the estimate is shortly after takeoff is what it keeps for the flight.
4. **A smaller prior (f1) stops the collapse, at a cost.** With the delay, f1 ends at 1.3-1.7 mOhm (40-50% low) instead of 0. With aligned samples, f1 ends at 3.4-3.5 mOhm (about 28% high), where px4 itself ends within 2-5% in the quiet and typical cells. A 100x smaller prior is not a free fix.

**Hypothesis (from reading the update, after the results):** when a load step reaches the voltage sample before the current sample, the update at that sample sees the same regressor as before. PX4's covariance update does not depend on the voltage at all, so the gate treats it like any other idle sample and usually rejects it. The reject branch sets the open-circuit estimate to `V + R*I`, which absorbs the voltage drop. One or two samples later the current sample rises while the voltage no longer changes. That update brings new information, so the gate accepts it with a large gain, and the data now say "the current rose, the voltage did not": the resistance goes to about 0. If the current sample arrives first, the first accepted update sees the same thing directly. After that, the gate holds the value. The hypothesis predicts that a one-sample offset in either direction is enough. It also predicts that turning off the re-anchoring helps only when the current comes late. Load steps that ramp over many samples instead of jumping should weaken it. PLAN-skew.md pre-registers a test of exactly these points before it is run.

## Limits

- One model battery, one flight profile and illustrative noise and delay. Real load steps ramp up rather than jump, and whether real power modules sample current and voltage at different times is not known. This simulation does not show that any real vehicle has such an offset.
- The 20 ms delay was one of two pre-registered timing levels. That the offset, not the noise, drives the persistent collapse is a reading of the per-cell figures, not a graded prediction.
- A from-boot, full-rate log of a vehicle that collapses (PLAN-trace.md) is still the only way to see what happens on a real vehicle; NOTE-trace-align.md adds a timing readout to it before any such log is seen.

Files: sim.py 0.1.0 and test_sim.py (unchanged since pre-registration), sim/ (this job's outputs).

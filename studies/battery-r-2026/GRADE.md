# battery-r-2026: graded

Graded by MV2 of Munim, Inc. (mv2@munimtech.com) on 2026-11-10, exactly against the table in
[PLAN.md](PLAN.md), which was committed before any log was read. The census ran once, in a sealed job
(`px4-log-health-battr-v1`, 2026-11-10), with battr_logs.py 0.1.0 and its synthetic tests passing
first. The grade, code and tests were made with AI agents working in MV2's name. Text CC BY 4.0,
code Apache-2.0.

Files: [battr-rows.jsonl](battr-rows.jsonl) (one row per requested log), [battr-summary.json](battr-summary.json)
(every figure below), [job-fetched.json](job-fetched.json) (what the job fetched, with sha256).

## The pass

- Flight Review's public list held 488,578 logs; 65,699 were eligible (real, at least 120 s, from
  2025-01-01, v1.15 or newer), from 10,291 vehicles.
- The pass requested 219 logs, one per vehicle, newest first, and stopped at the plan's limit of
  150 downloads ("max full logs"). 150 had the `internal_resistance_estimate` field and were
  downloaded, 57 did not have it, 12 were over 100 MB.
- 139 read OK (11 had no `battery_status` data). 75 were measurable. The others: no running
  estimator with valid current (56), fewer than 150 airborne samples (6), median airborne current
  under 0.5 A (2).
- Airborne time came from `vehicle_land_detected` on all 75. Vehicles: 58 quadrotors, 5 fixed wing,
  5 hexarotors, 6 VTOL, 1 octorotor. Releases v1.15.0 to v1.18.0.

## Predictions

| | Prediction | Holds if | Minimum | Result | Grade |
|---|---|---|---|---|---|
| R1 | Collapse to zero is not rare | collapsed / measurable >= 5 % | 20 measurable | 10 / 75 = **13.3 %** | **holds** |
| R2 | The estimate usually freezes early in flight | froze early / measurable >= 50 % | 20 measurable | 10 / 75 = **13.3 %** | **fails** |
| R3 | Where it does not collapse, the estimate is in a plausible range | median `r_end` of non-collapsed logs in 1-20 mOhm per cell | 20 non-collapsed | **2.74 mOhm per cell** (65 logs) | **holds** |
| R4 | The estimate does not track the flight's own data | Spearman rho(`r_end`, step fit) < 0.5 | 15 logs | rho = **0.767** (46 logs) | **fails** |
| R5 | Most vehicles use the estimate | `BAT1_R_INTERNAL` < 0 in >= 80 % | 20 measurable | 53 / 75 = **70.7 %** | **fails** |

Two predictions held and three failed. All five had enough logs to grade.

## What it means

- **R1 holds: the collapse is not one vehicle's problem.** On 10 of 75 measurable public flights
  (13 %), PX4's published resistance estimate was exactly 0 on every airborne sample of the
  second half of the flight. 9 of those 10 had `BAT1_R_INTERNAL` = -1, so PX4 used the estimate:
  9 of the 53 flights that rely on it (17 %) flew with the state of charge not corrected for load,
  which is what the forum report describes (low under load, recovering after landing). The 10 are
  on six boards (PX4 FMU-v6C 4, MicoAir H743 v2 3, CUAV 7-Nano, Holybro Kakute H7, CUAV X25 EVO
  1 each) and releases v1.16.0 to v1.17.0 (v1.16.0 1, v1.16.1 1, v1.16.2 3, v1.17.0 5).
- **R2 fails: the estimate does not usually freeze.** Only 10 of 75 stopped changing in the first
  quarter of the flight; the median log's last change came 85 % of the way through it. The
  "accept only if the covariance norm beats its best" rule does not, on its own, stop the
  estimate early on most vehicles.
- **R3 holds and R4 fails: when it works, the estimate is sensible and tracks the data.** On the
  65 non-collapsed flights the final estimate had a median of 2.74 mOhm per cell (p10 1.17, p90
  7.49), and none ended on the 5 mOhm start value. Against an independent fit from each flight's
  own voltage and current steps (median 2.43 mOhm per cell on 49 flights), the rank correlation
  was 0.77 on the 46 flights with both. We predicted it would not track; it does.
- **R5 fails: fewer vehicles use the estimate than we expected.** 53 of 75 run with
  `BAT1_R_INTERNAL` = -1; 17 set 0.002, 3 set 0.005, 1 set 0.003 and 1 set 0.

## Reported beside the grade (not pre-registered)

These were not predicted, so they are description, not tests.

- **Most collapses were already there at takeoff.** In 7 of the 10 collapsed flights the estimate
  was 0 at the first airborne sample, and in 6 of those it never changed in the air. In the other
  3 it reached 0 within 2.4, 6.4 and 15.2 s of takeoff, from 1.2, 0.005 and 11.6 mOhm per cell.
  23 of the 75 flights touched 0 at some point; 12 ended at 0.
- **The packs were not resistance-free.** The step fit was valid on 3 of the collapsed flights and
  gave 2.1, 3.9 and 8.7 mOhm per cell, the same range as the healthy flights.
- **The estimator kept accepting updates while publishing 0.** In all 10 collapsed flights the
  logged `estimation_covariance_norm` was lower at the end of the flight than at takeoff. In
  `Battery::updateInternalResistanceEstimation` (`src/lib/battery/battery.cpp`, PX4 main) that norm
  is only assigned when an update is accepted, and the same branch publishes
  `math::max(_RLS_est(1) / _params.n_cells, 0.f)`. So updates were accepted in flight, and at every
  logged sample (5 Hz) the result was clamped to 0: the internal estimate stayed at or below 0.
  This agrees with the source reading in the forum report.

## Limits

- `battery_status` is logged at about 5 Hz; the estimator runs on every battery update.
- Most PX4 logs start at arming, so a collapse that was already there at takeoff may have started
  on the ground before the log began. This census cannot say when.
- A wrong current-sensor scale, offset or sign can by itself drive the estimate negative, so a
  collapse is not by itself proof of an estimator fault. The step fit is a cross-check, not ground
  truth.
- One log per vehicle, newest first, from public Flight Review uploads: not a random sample of
  PX4 vehicles. 75 flights is a small sample.

## What follows (as the plan says)

- R1 held, so the counts above go to the forum topic that raised the question
  ([discuss.px4.io topic 49724](https://discuss.px4.io/t/battery-resistance-estimate-reaches-zero-and-persists-under-load-fmuv6c-bat1-r-internal-1/49724)).
- px4-log-health 0.3.9 gets a battery note for flights whose estimate collapsed while
  `BAT1_R_INTERNAL` is -1: the state of charge was then not corrected for load. It suggests no
  resistance value. Its measurement is battr_logs.py 0.1.0's, copied unchanged, and the build
  checks it against these rows.
- No PX4 issue yet. The open question for one is when the collapse starts (on the ground before
  takeoff, or in flight), which these logs mostly cannot show. A follow-up on vehicles this census
  did not read is pre-registered in [PLAN-onset.md](PLAN-onset.md).

MV2, Munim, Inc. (mv2@munimtech.com)

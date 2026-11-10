# battery-r-2026, third pass: do collapses at takeoff come with voltage that does not fall with current? (pre-registered)

Pre-registered by MV2 of Munim, Inc. (mv2@munimtech.com) on 2026-11-10, after the onset follow-up
was graded ([GRADE-onset.md](GRADE-onset.md)) and before this pass reads any log. The plan, code and
tests were made with AI agents working in MV2's name. Code Apache-2.0, text CC BY 4.0.

## Question

Every flight the census measures also gets a step fit: a least-squares fit of the pack's voltage
steps against its current steps between consecutive `battery_status` samples (dV = -R dI), with
its correlation `r`. A real battery's voltage falls when its current rises, so `r` should be well
below 0. In the onset pass, all 8 collapses that started before takeoff had `r` above -0.1 (5 of
them above 0), while only 11 of 60 non-collapsed flights did, and both collapses that started in
the air had valid, normal step fits. We saw this after looking at those rows, so it is a
hypothesis. If it holds on new data, most collapses at takeoff follow the estimator's inputs
(voltage that does not fall with current gives a best-fitting R of 0 or less, published as 0),
and the few that start in flight are the ones that would point at the estimator itself.

## Data

1. **Held-out rows (H):** the first census's `battr-rows.jsonl` (75 measurable flights, on `main`
   since 2026-11-10). Its step-fit `r` values are public, but we have not split them by collapse
   timing; the first grade reported only that 3 collapsed flights had a valid step fit. They are
   graded with the same rules as below, as a separate check.
2. **New vehicles (S):** a third pass over Flight Review's public list with the first census's
   rules (battr_logs.py 0.1.0, `build`): real logs of at least 120 s, from 2025-01-01, v1.15 or
   newer or no readable release; newest first, one log per vehicle. Every vehicle in
   `battr-rows.jsonl` and in `onset-rows.jsonl` is skipped. Same limits as before: 600 logs
   requested, 150 downloaded (100 MB each at most), 300 minutes, 6 s between requests, stop at the
   first 403/429/444. Only `battery_status`, `vehicle_land_detected` and `vehicle_status` are read;
   no position is read or written out; each log is deleted after it is read.

## Measure (steps.py 0.1.0)

The measurement is `onset.py` 0.1.0's, which is `battr_logs.py` 0.1.0's unchanged plus the onset.
For each measurable flight:

- `r`: the step fit's correlation (`step.r`); flights without one are left out of the counts
  that need it. **Flat**: `r` > -0.1.
- **Collapsed at takeoff**: collapsed (estimate 0 on every airborne sample of the second half)
  and `first_zero_s` = 0 (already 0 at the first airborne sample).
- **Collapsed later**: collapsed and `first_zero_s` > 0.
- **Valid step fit**: the census's rule (`r` <= -0.3, current range >= 5 A, >= 150 pairs).

## Predictions

Graded on the new vehicles (S1-S4) and, separately, on the held-out rows (H1-H4, same rules and
thresholds).

| id | prediction | holds if | minimum | my probability (S) |
|---|---|---|---|---|
| S1 | Collapses at takeoff come with flat data | flat / collapsed at takeoff with an `r` >= 60 % | 5 collapsed at takeoff with an `r` | 70 % |
| S2 | Flat data is uncommon on flights that do not collapse | flat / non-collapsed with an `r` <= 30 % | 20 non-collapsed with an `r` | 75 % |
| S3 | Flat data often goes with a collapse | collapsed / flat flights >= 25 % | 10 flat flights | 55 % |
| S4 | Collapses that start later come with a valid step fit | valid / collapsed later >= 50 % | 3 collapsed later | 50 % |

A prediction with fewer flights than its minimum is reported as not graded, not as failed.

## What follows

- The grade goes into `GRADE-steps.md`, exactly against this table, with `steps-rows.jsonl` and
  `steps-summary.json`.
- If S1 and S3 hold (and H1 does not contradict them), px4-log-health's battery note gets a
  pre-registered refinement: when a collapse comes with flat data, it says that the logged voltage
  did not fall with the logged current, and points first at the current and voltage sensing
  (PX4's battery calibration guide), before the estimator. No resistance value is suggested.
- If S4 holds, the in-flight collapses on clean data are written up with their logs for PX4 as a
  possible estimator issue (after reading PX4's CONTRIBUTING), with no fix proposed without a
  regression test.
- If S1 fails, the split we saw was chance, and that is reported.
- Either way, the forum topic that raised the question (discuss.px4.io topic 49724) gets the result.

## Limits, stated in advance

- `battery_status` is logged at about 5 Hz. A step fit from 5 Hz samples misses fast transients,
  and voltage and current may be filtered differently before they are logged.
- A flat `r` says the logged voltage and current disagree; it does not say which one is wrong.
- Public Flight Review uploads are not a random sample of PX4 vehicles. Collapses are about 14 %
  of measurable flights, so a pass of ~70 measurable flights gives ~10 collapses; S4 may well be
  not graded.

## Files

`steps.py`, `test_steps.py` (synthetic tests, run before any log is read), `job_fetch_steps.py`
(fetches `battr_logs.py` 0.1.0, `onset.py` 0.1.0, the first census's rows and the onset pass's rows
from px4-log-health's main, and `gnss_logs.py` 0.1.0 from px4-gps-lag's). The pass checks that this
file on main matches its local copy before it reads any log.

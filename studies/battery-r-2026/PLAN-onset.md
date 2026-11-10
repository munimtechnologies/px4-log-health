# battery-r-2026 follow-up: when does PX4's battery-resistance estimate collapse? (pre-registered)

Pre-registered by MV2 of Munim, Inc. (mv2@munimtech.com) on 2026-11-10, after the first census was
graded ([GRADE.md](GRADE.md)) and before this pass reads any log. The plan, code and tests were made
with AI agents working in MV2's name. Code Apache-2.0, text CC BY 4.0.

## Question

The first census found PX4's published `internal_resistance_estimate` at exactly 0 for the whole
second half of the flight on 10 of 75 measurable public flights. In 7 of the 10 it was already 0 at
the first airborne sample, and a forum report (discuss.px4.io topic 49724) saw it reach 0 at
0.74 A. In the code, the estimator runs whenever the current it is given is positive, armed or not,
and a negative estimate is published as 0. So does the collapse mostly start on the ground at low
current, before takeoff, or in flight? A maintainer would need that to judge whether the estimator
should run only above some current or only in flight. This pass answers it on **vehicles the first
census did not read**: the first census generated the hypothesis, so its vehicles are excluded.

## Sample

- Flight Review's public list, read once, with the first census's rules (battr_logs.py 0.1.0,
  `build`): real logs of at least 120 s, from 2025-01-01, v1.15 or newer or no readable release;
  newest first, one log per vehicle.
- Every vehicle in the first census's `battr-rows.jsonl` (all 219 requested) is skipped.
- Same limits as the first census: 600 logs requested, 150 downloaded (100 MB each at most),
  300 minutes, 6 s between requests, stop at the first 403/429/444.
- Only `battery_status`, `vehicle_land_detected` and `vehicle_status` are read. No position is read
  or written out. Each log is deleted after it is read.

## Measure (onset.py 0.1.0)

- Everything the first census measures, with its code unchanged (`battr_logs.measure_data`):
  measurable, collapsed, `r_end`, step fit and the rest.
- **Phase** of each `battery_status` sample of the measured battery: `airborne` when
  `vehicle_land_detected.landed` is 0; otherwise `armed on ground` or `disarmed` from
  `vehicle_status.arming_state` (2 = armed); `unknown` when the log lacks what is needed.
- **Onset** (collapsed logs only): the sample after the last sample, up to the last airborne
  sample, whose published estimate is above 0. If there is no such sample, the onset is
  `before the log` (the estimate was 0 on every logged sample up to the end of the flight).
  Recorded: the onset's phase, the measured current at the onset (if valid), and its time from the
  first airborne sample in seconds (negative = before takeoff).
- **Before takeoff** = onset `before the log`, or an onset time below 0.
- Also recorded: the phase of the log's first sample (most PX4 logs start at arming).

## Predictions (new vehicles only)

| id | prediction | holds if | minimum | my probability |
|---|---|---|---|---|
| O1 | The collapse is not rare on new vehicles either | collapsed / measurable >= 5 % | 20 measurable | 80 % |
| O2 | It usually starts before takeoff | before takeoff / collapsed >= 50 % | 5 collapsed | 65 % |
| O3 | Where the onset is in the log, it comes at low current | median current at the onset < 2.0 A | 3 collapsed with an onset current | 40 % |
| O4 | When it does not collapse, the estimate tracks the flight's own data | Spearman rho(`r_end`, step fit) >= 0.5, logs with `r_end` > 0 and a valid step fit | 15 such logs | 70 % |

A prediction with fewer logs than its minimum is reported as not graded, not as failed.

## What follows

- The grade goes into `GRADE-onset.md`, exactly against this table, with `onset-rows.jsonl` and
  `onset-summary.json`.
- If O2 and O3 hold, MV2 considers a PX4 issue with these logs as evidence (after reading PX4's
  CONTRIBUTING): the estimate collapses on the ground at low current, before the flight. It would
  not propose a fix without a regression test.
- If O2 fails, the collapse mostly starts in flight, and that is what gets reported.
- Either way, the forum topic that raised the question gets the result.

## Limits, stated in advance

- `battery_status` is logged at about 5 Hz; the estimator runs on every battery update.
- Logs that start at arming cannot show what happened before arming: those onsets are only
  `before the log`.
- A wrong current-sensor scale, offset or sign can drive the estimate negative by itself.
- Public Flight Review uploads are not a random sample of PX4 vehicles.

## Files

`onset.py`, `test_onset.py` (synthetic tests, run before any log is read), `job_fetch_onset.py`
(fetches battr_logs.py 0.1.0 and the first census's rows from px4-log-health's main, and
gnss_logs.py 0.1.0 from px4-gps-lag's). The pass checks that this file on main matches its local copy
before it reads any log.

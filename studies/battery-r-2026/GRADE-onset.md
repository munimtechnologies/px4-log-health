# battery-r-2026 follow-up: when does the collapse start? (graded)

Graded by MV2 of Munim, Inc. (mv2@munimtech.com) on 2026-11-10, exactly against the table in
[PLAN-onset.md](PLAN-onset.md), which was on `main` before any log was read (the job checked that
the plan on `main` matched its own copy byte for byte: sha256 `5b70d751...` in
[job-fetched-onset.json](job-fetched-onset.json)). The pass ran once, in a sealed job
(`px4-log-health-onset-v1`, 2026-11-10 06:38-07:10 UTC), with `onset.py` 0.1.0 on top of the first
census's unchanged `battr_logs.py` 0.1.0, and its synthetic tests passed before any log was read.
The grade, code and tests were made with AI agents working in MV2's name. Text CC BY 4.0, code
Apache-2.0.

Files: [onset-rows.jsonl](onset-rows.jsonl) (one row per requested log, no positions),
[onset-summary.json](onset-summary.json) (every figure below), [job-fetched-onset.json](job-fetched-onset.json).

## The pass

- Flight Review's public list held 488,578 logs; 65,699 were eligible (real, at least 120 s, from
  2025-01-01, v1.15 or newer). All 219 vehicles of the first census were skipped, leaving 63,340
  logs from 10,072 vehicles.
- The pass requested 225 logs, one per vehicle, newest first, and stopped at the plan's limit of
  150 downloads ("max full logs"). 150 had the estimator field and were downloaded, 60 did not
  have it, 15 were over 100 MB.
- 136 read OK and 71 were measurable. The others: no running estimator with valid current (54),
  median airborne current under 0.5 A (6), fewer than 150 airborne samples (5).
- The first logged sample was "armed on ground" in 54 of the 71, "disarmed" in 10 and unknown in 7.

## Predictions

| | Prediction | Holds if | Minimum | Result | Grade |
|---|---|---|---|---|---|
| O1 | The collapse is not rare on new vehicles either | collapsed / measurable >= 5 % | 20 measurable | 10 / 71 = **14.1 %** | **holds** |
| O2 | It usually starts before takeoff | before takeoff / collapsed >= 50 % | 5 collapsed | 8 / 10 = **80 %** | **holds** |
| O3 | Where the onset is in the log, it comes at low current | median current at the onset < 2.0 A | 3 with an onset current | **2.91 A** (4 logs: 0.76, 2.82, 2.99, 16.46 A) | **fails** |
| O4 | When it does not collapse, the estimate tracks the flight's own data | Spearman rho(`r_end`, step fit) >= 0.5 | 15 logs | rho = **0.792** (35 logs) | **holds** |

Three predictions held and one failed. All four had enough logs to grade.

## What it means

- **O1 holds: the rate repeats.** 10 of 71 measurable flights on vehicles the first census never
  read (14 %) had the published estimate at exactly 0 for the whole second half of the flight,
  against 10 of 75 (13 %) in the first census. 9 of the 10 had `BAT1_R_INTERNAL` = -1: 9 of the 59
  flights here that use the estimate (15 %). The 10 are on six boards (PX4 FMU-v6C 5, CubePilot
  Cube Orange+, WEGSOL V1.2, CORVON 743V1, MicoAir H743 v2 and PX4 FMU-v5 1 each), v1.16.0 to
  v1.17.0 (v1.17.0 8), 7 quadrotors and 3 VTOLs.
- **O2 holds: the collapse mostly starts before takeoff.** In 6 of the 10 the estimate was already
  0 at the first logged sample (these logs begin at arming), and in 2 it reached 0 on the ground
  while armed, 96.2 s and 4.4 s before takeoff. Only 2 began in the air, 4.6 s and 74.4 s after
  takeoff.
- **O3 fails: we cannot say the onset comes at low current.** Only 4 collapses have an onset
  inside the log. The two on the ground came at 0.76 A and 2.99 A, the two in the air at 2.82 A and
  16.46 A; the median, 2.91 A, is above the 2.0 A line. Four values are too few to say much either
  way, and the plan's rule is clear: this fails.
- **O4 holds: when it works, the estimate tracks the data.** On the 61 non-collapsed flights the
  final estimate had a median of 2.62 mOhm per cell (p10 0.29, p90 9.65), and it ranked the 35
  flights with a valid step fit like the step fit did (rho 0.79; the first census had 0.77).

## Reported beside the grade (not pre-registered)

These were not predicted, so they are description, not tests. They come from the step fit
both passes already record for every flight: a least-squares fit of the pack's voltage steps
against its current steps between consecutive samples (dV = -R dI), with its correlation `r`. A
real battery's voltage falls when its current rises, so a healthy log has `r` well below 0; the
fit counts as valid only when `r` <= -0.3, the current range is at least 5 A and there are at least
150 sample pairs.

- **The 8 collapses that started before takeoff came with data in which voltage did not fall
  as current rose.** Their step-fit `r` values were 0.464, 0.341, 0.213, 0.153, 0.072, 0.009,
  -0.003 and -0.010: all above -0.1, and 5 of the 8 above 0, which means the logged voltage tended
  to rise with the logged current. None had a valid step fit, although 7 of the 8 had a current
  range of 5 A or more. The two that started on the ground, where the onset is in the log, were
  at `r` = +0.46 (onset at 0.76 A) and +0.15 (onset at 2.99 A).
- **The 2 collapses that started in the air came with normal-looking data.** Their step fits
  were valid: 4.05 mOhm per cell (`r` = -0.36) and 2.81 mOhm per cell (`r` = -0.53), so those packs
  showed ordinary resistance and the estimate still went to 0.
- **Among the 61 non-collapsed flights**, 11 of the 60 with an `r` (18 %) had `r` above -0.1,
  and 37 of 61 had a valid step fit.

What this suggests, to be tested: the estimator fits V = OCV - R I. When the logged voltage does
not fall with the logged current, the best-fitting R is 0 or negative, and PX4 publishes
`max(R / n_cells, 0)`, so 0. In those flights the estimate may simply be following its inputs,
and the question becomes why voltage and current disagree (current-sensor scale or offset, a
current that is not the pack's, or voltage and current sampled or filtered differently). The
in-flight collapses on clean data are the ones that would point at the estimator itself. Because
we noticed this split after looking at these rows, it is a hypothesis here, not a finding. A
third pass on new vehicles, and a check of the first census's rows that we had not split this
way, are pre-registered in [PLAN-steps.md](PLAN-steps.md).

## Limits

- `battery_status` is logged at about 5 Hz; the estimator runs on every battery update. A step fit
  from 5 Hz samples misses fast transients.
- 6 of the 8 before-takeoff collapses were already 0 at the first logged sample, so their onset
  is before the log and cannot be placed.
- A wrong current-sensor scale, offset or sign can by itself drive the estimate negative.
- One log per vehicle, newest first, from public Flight Review uploads: not a random sample of
  PX4 vehicles. 71 flights and 10 collapses are small samples.

## What follows (as the plan says)

- O2 held and O3 failed. The plan said MV2 would consider a PX4 issue only if both held, so none is
  opened now. If the third pass confirms the voltage-current split, the useful report to PX4 is
  narrower: which collapses come with inconsistent sensor data and which do not.
- The forum topic that raised the question
  ([discuss.px4.io topic 49724](https://discuss.px4.io/t/battery-resistance-estimate-reaches-zero-and-persists-under-load-fmuv6c-bat1-r-internal-1/49724))
  gets this result.

MV2, Munim, Inc. (mv2@munimtech.com)

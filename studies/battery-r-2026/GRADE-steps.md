# battery-r-2026, third pass: do collapses at takeoff come with voltage that does not fall with current? (graded)

Graded by MV2 of Munim, Inc. (mv2@munimtech.com) on 2026-11-10, exactly against the table in
[PLAN-steps.md](PLAN-steps.md), which was on `main` before any log was read. The job checked that
the plan on `main` matched its own copy byte for byte (sha256 `90057a08...` for both, in
[job-fetched-steps.json](job-fetched-steps.json)). The pass ran once, in a sealed job
(`px4-log-health-steps-v1`, 2026-11-10 13:24-13:59 UTC), with `steps.py` 0.1.0 on top of the
unchanged `onset.py` 0.1.0 and `battr_logs.py` 0.1.0. Its synthetic tests passed before any log was
read. The grade, code and tests were made with AI agents working in MV2's name. Text CC BY 4.0,
code Apache-2.0.

Files: [steps-rows.jsonl](steps-rows.jsonl) (one row per requested log, no positions),
[steps-summary.json](steps-summary.json) (every figure below), [job-fetched-steps.json](job-fetched-steps.json).

**Result in one line:** on new vehicles the split held (S1, S2, S3 hold; S4 not graded), but on
the held-out first-census rows it did not (H1 fails: 2 of 7). Pooled, flat data goes with about
half of the collapses at takeoff, not most of them. So the battery note is **not** changed, as
the plan says.

## The pass (S)

- Flight Review's public list held 488,578 logs; 65,699 were eligible (real, at least 120 s, from
  2025-01-01, v1.15 or newer). The 444 vehicles of the first census and the onset pass were
  skipped, leaving 61,406 logs from 9,847 vehicles.
- The pass requested 237 logs, one per vehicle, newest first, and stopped at the plan's limit of
  150 downloads. 150 had the estimator field and were downloaded, 71 did not have it, 16 were over
  100 MB.
- 139 read OK and 65 were measurable. The others: no running estimator with valid current (58),
  median airborne current under 0.5 A (10), fewer than 150 airborne samples (6). All 65 had a step
  fit `r`.
- 6 of the 65 collapsed (9.2 %; 14.1 % and 13.3 % in the earlier passes), all with
  `BAT1_R_INTERNAL` = -1 (6 of the 60 flights here that use the estimate). 5 were already 0 at the
  first airborne sample ("at takeoff"), 1 reached 0 later.

## Predictions

Flat means step-fit `r` > -0.1. Valid step fit means `r` <= -0.3, current range >= 5 A and at
least 150 pairs.

| | Prediction | Holds if | Minimum | New vehicles (S) | Grade | Held-out first census (H) | Grade |
|---|---|---|---|---|---|---|---|
| 1 | Collapses at takeoff come with flat data | flat / collapsed at takeoff >= 60 % | 5 | 4 / 5 = **80 %** | **holds** | 2 / 7 = **28.6 %** | **fails** |
| 2 | Flat data is uncommon without a collapse | flat / non-collapsed <= 30 % | 20 | 8 / 59 = **13.6 %** | **holds** | 9 / 65 = **13.8 %** | **holds** |
| 3 | Flat data often goes with a collapse | collapsed / flat >= 25 % | 10 | 4 / 12 = **33.3 %** | **holds** | 3 / 12 = **25.0 %** | **holds** (on the line) |
| 4 | Collapses that start later have a valid step fit | valid / collapsed later >= 50 % | 3 | 1 later collapse | **not graded** | 2 / 3 = **66.7 %** | **holds** |

On new vehicles three predictions held and one could not be graded. On the held-out rows three
held and H1 failed.

## What it means

- **S1 holds, H1 fails: flat data explains some collapses at takeoff, not most.** On the new
  vehicles, 4 of the 5 collapses at takeoff came with flat data (`r` = +0.62, +0.41, +0.39,
  +0.04; the fifth, -0.22, was not flat but not valid either). On the first census's rows, only 2
  of 7 did (`r` = +0.10, +0.02); the other 5 had voltage that fell with current (`r` = -0.16,
  -0.19, -0.26, -0.62, -0.83), and one of them had a valid step fit (3.9 mOhm per cell, on a
  vehicle with `BAT1_R_INTERNAL` set to 0.005, so PX4 was not using the estimate). Pooled over
  the two sets, **6 of 12** collapses at takeoff came with flat data. The onset pass's 8 of 8,
  which suggested the idea, was not a fair test, and it did not repeat on rows we had not split.
- **S2 and H2 hold: flat data is uncommon on flights that do not collapse** (13.6 % and 13.8 %).
- **S3 and H3 hold: flat data is a risk marker.** A collapse followed on 33 % and 25 % of flat
  flights, against 3.8 % (2 of 53) and 11.1 % (7 of 63) of the others. Pooled: 7 of 24 flat
  flights (29 %) against 9 of 116 (7.8 %), about 4 times the rate.
- **S4 not graded, H4 holds: collapses that start in the air mostly come with normal-looking
  data.** The new pass had only 1 such collapse (a valid fit, though its 0.12 mOhm per cell is
  implausibly low). In the first census, 2 of 3 had valid fits (8.7 and 2.1 mOhm per cell). With
  the onset pass's 2 of 2, these are the cases that would point at the estimator itself, but
  there are still too few to grade on new data.

## Reported beside the grade (not pre-registered)

- 5 of the 12 pooled collapses at takeoff had a current range under 5 A over the flight (3 of 5
  in S, 2 of 7 in H); 19 of the 59 non-collapsed flights in S (32 %) did too. Small numbers; we
  do not read anything into it.
- In S, one collapse counted "at takeoff" by the plan's rule (0 at the first airborne sample)
  recovered briefly and settled at 0 1.4 s after takeoff; the plan's rule is used as written.

## What follows (as the plan says)

- **No change to the battery note.** The plan made the refinement ("the logged voltage did not
  fall with the logged current; check current and voltage sensing first") conditional on S1 and
  S3 holding without H1 contradicting them. H1 contradicts them, so px4-log-health keeps its
  current wording: a collapse is a note and a reason to look at the battery plots, not proof of
  an estimator fault.
- **No PX4 issue.** S4 could not be graded. The in-flight collapses on normal-looking data remain
  the estimator-side lead, now 5 of 6 across the three passes (counted by each pass's own rule),
  but that is too few for a claim.
- What would test the estimator directly is not another census pass but a replay: run PX4's
  recursive least-squares update offline on the logged voltage and current of the collapsed
  flights and see whether it reproduces the published estimate. That would be pre-registered
  separately.
- The forum topic that raised the question
  ([discuss.px4.io topic 49724](https://discuss.px4.io/t/battery-resistance-estimate-reaches-zero-and-persists-under-load-fmuv6c-bat1-r-internal-1/49724))
  gets this result.

## Limits

- `battery_status` is logged at about 5 Hz. A step fit from 5 Hz samples misses fast transients,
  and voltage and current may be filtered differently before they are logged.
- A flat `r` says the logged voltage and current disagree; it does not say which one is wrong.
- One log per vehicle, newest first, from public Flight Review uploads: not a random sample of
  PX4 vehicles. 12 pooled collapses at takeoff and 4 later collapses are small numbers.

MV2, Munim, Inc. (mv2@munimtech.com)

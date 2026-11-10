# battery-r-2026, replay: run PX4's resistance estimator offline on the flights that collapsed (pre-registered)

Pre-registered by MV2 of Munim, Inc. (mv2@munimtech.com) on 2026-11-10, before any log is read for
this step. The job that runs it checks that this file on `main` matches its own copy byte for byte
before it opens a log. The plan, code and tests were made with AI agents working in MV2's name.
Text CC BY 4.0, code Apache-2.0.

## Why

Three graded passes ([GRADE.md](GRADE.md), [GRADE-onset.md](GRADE-onset.md),
[GRADE-steps.md](GRADE-steps.md)) found PX4's published battery internal-resistance estimate stuck
at 0 for the whole second half of the flight on 26 of 211 measurable public flights (9-14 % per
pass). Flat logged data (voltage that does not fall when current rises) is a risk marker, but it
goes with only about half of the collapses at takeoff, and 5 of the 6 collapses that began in the
air came with normal-looking data. A census cannot separate "the inputs say R <= 0" from "the
estimator's own rules drive it there". A replay can: run PX4's update on the same logged voltage and
current, and see whether it collapses too.

## What PX4's code does (read on `main`, 2026-11-10)

`src/lib/battery/battery.cpp`, `Battery::updateInternalResistanceEstimation()`, runs on every
battery update with `current_a > FLT_EPSILON` (the analog path samples at 100 Hz; the logger keeps
`battery_status` at 200 ms, about 5 Hz). With x = [1, -I]:

- prediction `x' * est`, error `V - prediction`, gain `P x / (LAMBDA + x' P x)`, candidate
  covariance `(P - gain x' P) / LAMBDA`, `LAMBDA = 0.95`;
- "Only update if estimation improves": the candidate is accepted only if its norm
  sqrt(P00^2 + 2 P10^2 + P11^2) is smaller than the stored `_estimation_covariance_norm`;
- if rejected, only the OCV estimate moves: `_RLS_est(0) = voltage_v + _RLS_est(1) * current_a`;
- published: `max(_RLS_est(1) / n_cells, 0)`;
- reset (`resetInternalResistanceEstimation`) on every update during the first 2 s after the pack
  is connected: est = [V, 0.005 n], P = diag(1.5 n, 0.1 n).

Three consequences follow from the code alone. They are stated here so the data can check them:

1. **The norm is a ratchet.** The stored norm changes only when an update is accepted, and an
   update is accepted only when it lowers the norm. Between resets it never rises, so the
   forgetting factor can never re-inflate the covariance: the estimator only gets harder to
   update as the flight goes on.
2. **The norm has a floor of 1 - LAMBDA = 0.05, whatever the data.** In information form an
   accepted step is J' = LAMBDA J + x x'. The first entry of x is always 1, so J00 tends to
   1 / (1 - LAMBDA) = 20 from below and never reaches it; for a positive definite matrix
   P00 = (J^-1)00 >= 1 / J00 > 0.05, and the norm is at least P00. (In the first census's 75
   measurable flights the norm at the last airborne sample was between 0.0505 and 2.93, and it was
   lower than at the first airborne sample in 74; the other rose from 0.204 to 0.212, which a
   reset in flight would explain. We noticed this while writing this plan, so C1 and C2 below are
   checks, not discoveries.)
3. **The gate looks at current only.** P, its candidate and the norm depend only on the current
   samples, never on voltage or the prediction error. The gate cannot reject an outlier or
   inconsistent voltage; it only selects updates by the current pattern.

## Data

No new sampling. The flights are the measurable rows of the three earlier passes
(`battr-rows.jsonl`, `onset-rows.jsonl`, `steps-rows.jsonl` on `main`): 211 flights, one per
vehicle, 26 collapsed. Collapsed flights are re-read first, then the others, in pass and row order.
Each log is downloaded from Flight Review again, 6 s apart, read for `battery_status`,
`vehicle_land_detected` and `vehicle_status` only (no positions), and deleted. At most 230
downloads, 300 minutes; the pass stops at the first block (HTTP 403/429/444).

## Method

For each flight, `replay.py` 0.1.0:

- recomputes the census measurement with the unchanged `battr_logs.py` 0.1.0 (same battery
  instance, same airborne samples, same collapse rule: published estimate 0 for the whole second
  half of the airborne samples; same step fit and flat rule, `r` > -0.1);
- checks the **logged** `estimation_covariance_norm` (the real estimator at its full rate): every
  rise between consecutive logged samples, and whether a reset explains it (a logged disconnection
  or voltage under 2.1 V in the 3 s before, a cell-count change, a value within 1 % of the reset
  norm 1.5033 n, or a previous value of 0); its smallest positive value; and the share of airborne
  sample pairs in which it fell (at least one accepted update in between);
- **replays** PX4's update line for line in float32 over the logged samples, as if the pack had
  been connected when the log started (reset on every sample of the first 2 s, as PX4 does), and
  applies the census's collapse rule to the replayed published value;
- runs the same least squares **without the gate** (float64) and records the median of its raw
  R / n over the second half of the airborne samples.

The port is checked by synthetic tests before any log is read: one update worked by hand
(appendix), a rejected update, convergence on clean data, zero on voltage that rises with
current, the ratchet, the floor, the gate's independence from voltage, a freeze under constant
current, resets after a disconnection, and the grading rules.

## Predictions

Flat: step-fit `r` > -0.1. Steep: `r` <= -0.1. "Collapsed" for a replay uses the census rule on the
replayed published value. A prediction is not graded if its group is under the minimum.

| | Prediction | Holds if | Minimum |
|---|---|---|---|
| C1 | The logged norm is a ratchet on real flights | flights with no unexplained rise / replayed flights >= 95 % | 50 |
| C2 | The logged norm stays above the floor | flights with smallest positive norm >= 0.0499 / replayed flights >= 99 % | 50 |
| P1 | The replay does not invent collapses | replay not collapsed / non-collapsed flights >= 85 % | 30 |
| P2 | Collapses on flat data follow their inputs | replay collapsed / flat collapsed flights >= 60 % | 6 |
| P3 | Collapses on steep data do not come from the logged data | replay not collapsed / steep collapsed flights >= 60 % | 6 |
| P4 | The replay tracks the real estimator where it works | Spearman rho(replayed end value, logged end value) >= 0.5 on non-collapsed flights with a valid step fit and a positive logged end value | 20 |
| P5 | Without the gate, steep collapsed flights give a positive R | plain-RLS median R > 0 / steep collapsed flights >= 60 % | 6 |

C1 and C2 test whether the fleet runs the code as read (they are near-certain if it does). P1 and
P4 test whether a 5 Hz replay is a fair stand-in for the real estimator. P2, P3 and P5 are the
questions.

## What each outcome means (fixed now)

- **P2 and P3 hold (with P1):** collapses on flat data are the estimator following inconsistent
  inputs; collapses on steep data cannot be reproduced from what the log holds, so their cause is
  in what the log does not keep (the estimator's state before logging started, or fast dynamics
  between the 5 Hz samples). Next step: ask for a log with `battery_status` at full rate, or a
  SITL replay at 100 Hz. No PX4 issue on this alone.
- **P3 fails and P5 holds:** on steep data the gated replay collapses while the same least squares
  without the gate stays positive, so the gate's selection of updates is implicated. MV2 would
  then draft a PX4 issue with the replay code and the flights as evidence, after reading PX4's
  contributing rules, and say plainly that it comes from 5 Hz logs.
- **P3 fails and P5 fails:** the logged data at 5 Hz itself drives the least squares negative even
  where the step fit is positive. This points at the model (a fixed OCV over the forgetting window
  while the pack discharges and relaxes), not the gate. Reported as such; no issue without a
  mechanism test.
- **P1 or P4 fails:** the 5 Hz replay is not a fair stand-in. P2, P3 and P5 are still reported but
  not interpreted.
- **C1 or C2 fails:** some released firmware does not run the code as read. The flights that break
  it are listed by release; the rest of the grade is reported for the others as description only.

Reported beside the grade, not predicted: the logged norm at the first airborne sample and at the
end relative to the floor, the share of airborne sample pairs with an accepted update (logged and
replayed), every collapsed flight's row, and whether the recomputed census agrees with the earlier
rows.

## Limits

- The replay runs at the logged ~5 Hz, the estimator at its sample rate (100 Hz on the analog path,
  the message rate on others). LAMBDA = 0.95 per update means a memory of about 0.2 s on PX4 and
  about 4 s in the replay. This is why P1 and P4 come first.
- Logs start at arming; the estimator's state before that is unknown, so the replay starts fresh
  at the first logged sample. Most collapses start before takeoff (O2), so this matters.
- Logged voltage and current are what the library used at that moment, but not every sample it
  used.
- 211 flights and 26 collapses from public uploads, newest first, one per vehicle: not a random
  sample. Some logs may no longer download.

## Appendix: one update worked by hand (test_one_update_by_hand)

n = 4, reset at 16 V: est = [16, 0.02], P = diag(6, 0.4), norm = 6.0133. Then V = 15.6 V at 10 A:
x = [1, -10]; prediction 16 - 10 x 0.02 = 15.8; error -0.2; P x = [6, -4]; x' P x = 46; gain =
[6, -4] / 46.95 = [0.127796, -0.085197]; est = [15.974441, 0.0370394]; candidate P =
[[5.508660, 0.538086], [0.538086, 0.0623284]], norm 5.5613 < 6.0133, so accepted; published
0.0370394 / 4 = 9.26 mOhm per cell.

MV2, Munim, Inc. (mv2@munimtech.com)

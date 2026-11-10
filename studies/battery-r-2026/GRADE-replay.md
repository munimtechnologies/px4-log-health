# battery-r-2026, replay: run PX4's resistance estimator offline on the flights that collapsed (graded)

Graded by MV2 of Munim, Inc. (mv2@munimtech.com) on 2026-11-10, exactly against the table in
[PLAN-replay.md](PLAN-replay.md), which was on `main` before any log was read. The job checked that
the plan on `main` matched its own copy byte for byte (sha256 `0128b370...` for both, in
[job-fetched-replay.json](job-fetched-replay.json)). The replay ran once, in a sealed job
(`px4-log-health-replay-v1`, 2026-11-10 19:03-20:06 UTC), with `replay.py` 0.1.0 on top of the
unchanged `battr_logs.py` 0.1.0. Its 21 synthetic tests (including the update worked by hand in the
plan's appendix) passed before any log was read. The grade, code and tests were made with AI agents
working in MV2's name. Text CC BY 4.0, code Apache-2.0.

Files: [replay-rows.jsonl](replay-rows.jsonl) (one row per flight, no positions),
[replay-summary.json](replay-summary.json) (every figure below), [job-fetched-replay.json](job-fetched-replay.json).

**Result in one line:** a 5 Hz replay of PX4's update, started fresh at the start of each log,
tracks the real estimator where it works (P1, P4 hold) but reproduces **none of the 11 collapses
on steep data** (P3 holds), and the same least squares without the gate gives a positive
resistance on 10 of them (P5 holds). It also reproduces only **6 of the 15 collapses on flat data**
(P2 fails). Most collapses cannot be rebuilt from what a default PX4 log holds. No PX4 issue
follows from this.

## The pass

- 211 flights were requested (the measurable rows of the three earlier passes, one per vehicle,
  26 collapsed). All 211 downloaded and all had the estimator field. The recomputed census
  measurement equalled the earlier rows on all 211 (0 different).
- Steep: step-fit `r` <= -0.1 (11 collapsed flights). Flat: `r` > -0.1 (15 collapsed flights).

## Predictions

| | Prediction | Holds if | Minimum | Result | Grade |
|---|---|---|---|---|---|
| C1 | The logged norm is a ratchet on real flights | no unexplained rise / replayed >= 95 % | 50 | 201 / 211 = **95.3 %** | **holds** (close to the line) |
| C2 | The logged norm stays above the floor | smallest positive norm >= 0.0499 / replayed >= 99 % | 50 | 211 / 211 = **100 %** | **holds** |
| P1 | The replay does not invent collapses | replay not collapsed / non-collapsed >= 85 % | 30 | 183 / 185 = **98.9 %** | **holds** |
| P2 | Collapses on flat data follow their inputs | replay collapsed / flat collapsed >= 60 % | 6 | 6 / 15 = **40.0 %** | **fails** |
| P3 | Collapses on steep data do not come from the logged data | replay not collapsed / steep collapsed >= 60 % | 6 | 11 / 11 = **100 %** | **holds** |
| P4 | The replay tracks the real estimator where it works | Spearman rho >= 0.5 | 20 | rho = **0.713** on 109 flights | **holds** |
| P5 | Without the gate, steep collapsed flights give a positive R | plain-RLS median R > 0 / steep collapsed >= 60 % | 6 | 10 / 11 = **90.9 %** | **holds** |

Six of seven held; P2 failed.

## What it means

**The plan's table has no row for this outcome.** It fixed meanings for "P2 and P3 hold", for
the two "P3 fails" cases (the only route to a PX4 issue), for "P1 or P4 fails" and for "C1 or C2
fails". Here P3 holds and P2 fails. So neither "P3 fails" branch applies, and there is **no PX4
issue**. The rest is said as narrowly as the numbers allow:

- **C1 and C2: the fleet runs the code as read.** On 201 of 211 flights the logged covariance norm
  never rose except where a reset explains it, and on all 211 it stayed above 0.05. The 10
  exceptions are listed below. They do not change the grade, because C1 held.
- **P1 and P4: a 5 Hz replay is a fair stand-in where the estimator works.** It invented a
  collapse on 2 of 185 flights, and its end value ranked with the logged one at rho 0.71. One of
  the two had flat data and a logged end value of 0 that the census rule does not count as a
  collapse. The other is worth noting. A v1.17.0 quadrotor had clean data (step fit 8.5 mOhm per
  cell, `r` = -0.37), PX4's own estimate ended at 8.8, and the ungated least squares gave 6.3. Yet
  the gated replay, started fresh, went to 0 and stayed there. So on one flight, PX4's gated
  update collapsed on clean data depending only on where it started. That is one flight, and the
  replay's start is not PX4's.
- **P3 and P5: on steep data the collapses are not in the logged data.** On all 11 steep
  collapsed flights the replay stayed positive (median end value 3.3 mOhm per cell), and the
  plain least squares was positive on 10 (the eleventh was -0.12 mOhm). So the voltage and current
  in the log, replayed through PX4's own update, do not drive the estimate to 0. The cause is in
  something the log does not hold: the estimator's state before logging started, or the samples
  between the 5 Hz logged ones. Five of these 11 reached 0 in the air, not at takeoff. On those
  five the replay ended at 3.1, 8.4, 3.3, 3.4 and 0.16 mOhm per cell.
- **P2: on flat data, the inputs alone rebuild fewer than half of the collapses.** On 6 of 15
  the replay collapsed too. On the other 9 the gated replay stayed positive (0.02 to 124 mOhm per
  cell). On 5 of those 9 the ungated least squares on the same samples was negative (-0.03 to
  -398 mOhm), so the data pointed down while the gated update did not follow. With the gate, the
  published value depends on where the estimator started and which updates it let through, not
  only on the data.

## Reported beside the grade (not pre-registered)

These figures come from fields the plan said it would report, read after grading. They are
descriptions, not tests.

- **In flight, PX4's estimate is almost frozen.** In the median flight the logged norm fell
  (meaning at least one update was accepted) in only 1.5 % of the 0.2 s airborne intervals (p75
  3.0 %, p90 6.8 %). At the end of the flight it was within 17 % of the 0.05 floor in the median
  flight. That is what the ratchet predicts: once the norm is near its floor, almost no update
  can lower it further.
- **The estimate is often formed before the log begins.** On 90 of 211 flights (43 %) the norm
  was already below 0.15 (3 times the floor) at the first logged sample. That means the estimator
  had converged on the ground before arming, which is when a default log starts (`SDLOG_MODE` 0).
  The share was about the same on collapsed flights (13 of 26) and on the others (77 of 185), so
  this alone does not say which flights collapse.
- **Where the replay starts from PX4's own state, it comes closer.** 24 logs start exactly at
  PX4's reset norm (1.5033 n), so the estimator was fresh at the start of the log and the replay
  starts where PX4 did. Three of them collapsed. On the two steep ones the replay ended at 0.03 and
  0.16 mOhm per cell: almost 0, though not 0 by the census rule. On the flat one it ended at 7.8.
  Three flights are far too few to grade.
- **The 10 flights that broke the ratchet** came from v1.15.0 (4 flights from FMU-V5X 12S vehicles,
  one rise each of 5.8-13 %), v1.17.0 (4), v1.16.0 (1) and a `v2.0.1` build (1). One v1.17.0
  flight's norm rose 86 times without a logged explanation (once to 46.5 times its previous value)
  and fell in 40 % of airborne intervals. That flight is not running the estimator as read, or is
  resetting in a way the log does not show.

## What follows

- **No PX4 issue.** The plan allowed one only if the gated replay collapsed on steep data while
  the ungated fit stayed positive. The replay did not collapse on any of them.
- **The next real test needs a different log, not another pass.** That means logging from boot
  (`SDLOG_MODE` = 2, "from boot until shutdown") with `battery_status` at full rate (a line
  `battery_status 0` in `etc/logging/logger_topics.txt` on the SD card, per PX4's
  [logging docs](https://docs.px4.io/main/en/dev_log/logging.html)). A replay of such a log starts
  from PX4's own reset and sees every sample. The forum topic that raised the question
  ([discuss.px4.io topic 49724](https://discuss.px4.io/t/battery-resistance-estimate-reaches-zero-and-persists-under-load-fmuv6c-bat1-r-internal-1/49724))
  has a vehicle that collapses. If its owner can share one such log, a replay on it would be
  pre-registered before it is read.
- **For owners, today:** if your log shows the estimate at 0 from takeoff, the log cannot tell
  you why. The value was most likely set on the ground before the log started, and the gate then
  held it. Check the current and voltage sensing first (as before), then log from boot.

## Limits

- The replay runs at the logged ~5 Hz, while PX4 updates at its sample rate (100 Hz on the analog
  path). P1 and P4 say the stand-in is fair where the estimator works. They cannot say it is fair
  where it collapses.
- Default logs start at arming, so the replay starts fresh where PX4 had already been running.
  This is the main reason the replay cannot rebuild most collapses.
- 211 flights and 26 collapses from public Flight Review uploads, one per vehicle, newest first:
  not a random sample, and 11 + 15 is small.

MV2, Munim, Inc. (mv2@munimtech.com)

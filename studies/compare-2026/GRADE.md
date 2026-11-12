# compare-2026: grade of the first null-pair job

Graded 2026-11-12 against [PLAN.md](PLAN.md) (pre-registered 2026-11-11). By MV2 of Munim, Inc.; made with AI
agents working in his name. Text and data CC BY 4.0.

Job: `px4-log-compare-null-v1`, run 2026-11-12 15:54 to 16:31 UTC. It checked that PLAN.md, pairs.py,
compare.py and test_compare.py on main were the copies it ran (commit `6ded898`), passed all 27 tests in
test_compare.py, read Flight Review's listing (489,563 entries, 62,352 eligible logs, 6,104 vehicles with a
pair), downloaded 188 logs and stopped at its target of 40 null pairs. Files: [pairs-rows.jsonl](pairs-rows.jsonl),
[summary.json](summary.json), [job-fetched.json](job-fetched.json), [tests.txt](tests.txt).

## Read this first: 22 of the 40 null pairs are one file uploaded twice

In 22 of the 40 null pairs, both logs have the same sha256: one ULog file uploaded to Flight Review twice under
two log ids. On an identical file every figure is identical, so no rule can fire. PLAN.md did not exclude
such pairs. That was our mistake, and it makes the pre-registered grade below look better than the data
supports. The pre-registered grade is reported as computed, and next to it the same checks on the 18 pairs of
distinct flights. **Only the distinct-flight column measures flight-to-flight noise.** The noise percentiles in
summary.json (`null_spread`) include the duplicates and understate it; use the figures in this file instead.

Duplicates are common in the sample overall: of the 71 non-upgrade pairs where both files were read (40 null,
19 config, 12 unmeasured), 29 (41%) were byte-identical. None of the 19 config pairs or 11 upgrade pairs was.

## The grade

| # | Prediction | As pre-registered (40 pairs) | Distinct flights only (18 pairs) |
|---|---|---|---|
| G1 | at least 30 null pairs | **passed** (40) | **not met** (18): a case series, no claim |
| P1 | GPS lag: median change 10 ms or less and C1 fires on 10% or fewer (12 pairs) | **held**: 0 of 16, median 0.0 ms | too few: 0 of 7, median 6.3 ms, largest 16.5 ms |
| P2 | Vibration: C3 fires on 15% or fewer (12 pairs) | **held**: 3 of 37 (8.1%) | **would fail**: 3 of 16 (18.8%) |
| P3 | Motor saturation: C5 fires on 10% or fewer (12 pairs) | **held**: 0 of 28 | **held**: 0 of 12 |
| P4 | Compass: C6 fires on 25% or fewer (10 pairs) | **held**: 2 of 15 (13.3%) | too few: 2 of 6 |
| P5 | Battery: C7 fires on 10% or fewer (10 pairs) | too few: 0 of 8 | too few: 0 of 5 |
| P6 | some vehicle item worse on 30% or fewer (30 pairs) | **held**: 4 of 40 (10%) | too few: 4 of 18 (22%) |
| P7 | some setting differs on 20% to 60% (30 pairs) | **held**: 19 of 59 (32%) | **held**: 19 of 37 (51%) |
| U1 | upgrade pairs: lag change within 5 ms of published (10 of 11) | **held**: 11 of 11, all identical | (no duplicates among them) |

Other rules on the 18 distinct pairs (not predicted): clipping C4 fired on 2 of 16, in-flight battery warning
on 2 of 15, failsafe in flight on 3 of 18, GPS delay status C1b and innovation C2 on 0 of 7 each.

**Our reading.** The rules do not fire on most pairs of distinct, unchanged flights: 12 of the 18 had no rule
fire on a vehicle item at all. But 18 pairs cannot pin a false-alarm rate down: 3 of 16 is consistent with anything from about 4%
to 46% (95% interval). One pair (vehicle `008c15281dd8`) holds 5 of the 12 firings: its median vibration fell
from 2.2 to 0.04 m/s^2 between two logs of 8 and 9 minutes in the air, while clipping, a compass class change, a battery warning
and a failsafe appeared. Two logs that different are probably not two comparable flights, which is what the
tool should flag, but it counts against the rules here as the plan says it must.

Vibration on distinct pairs: half moved by 9% or less (median |log2 ratio| 0.124); the three that fired moved
1.55x and 1.96x up and 51x down. GPS lag moved by 1.5, 3.7, 6.0, 6.3, 7.6, 12.3 and 16.5 ms, all well inside
C1's 30 ms.

**P5 and the battery question in topic 49724.** None of the 5 distinct pairs with the battery item readable
had a collapse on either flight, so this job says nothing about whether a collapse repeats on an unchanged
vehicle. Among the upgrade pairs, one vehicle collapsed on both flights and two collapsed only after the
v1.16 to v1.17 upgrade; those pairs changed firmware, so they are not evidence either way.

**Upgrade pairs (described).** On the 11 v1.16 to v1.17 pairs, C1 fired on 7 (5 better, 2 worse) and C2 on 8
(7 better). Config pairs fired more often than distinct null pairs on most items, as expected when settings
change: failsafe 7 of 19, vibration 6 of 18, eph 5 of 13.

## What follows

PLAN.md's rule for a failed prediction (raise that rule's threshold to the null 95th percentile, then check on
fresh pairs) needs a null set that measures noise. 16 distinct pairs are too few to set a 95th percentile, so
no threshold changes now. Instead the study is repeated on distinct flights of fresh vehicles, pre-registered
before any of its logs are read: [PLAN-distinct.md](PLAN-distinct.md). It drops re-uploads, keeps every rule
and threshold, and also measures how common re-uploads are across all of Flight Review's public listing.

Until that is graded, compare mode's false-alarm figures are the distinct-flight ones above, with their counts.

Lesson recorded for our other studies: anyone mining public PX4 logs should drop byte-identical files first.
We will check MV2's earlier census studies for duplicates and report what changes.

MV2, Munim, Inc. (mv2@munimtech.com)

# GRADE-distinct: compare-2026 null study on distinct flights

Graded 2026-11-17 from job `px4-log-compare-distinct-v1` (pairs_distinct.py 0.1.0; ran 2026-11-17 01:45-02:45 UTC, exit 0).
Plan: `PLAN-distinct.md` (pre-registered; published before the job read any log). Every number below is copied from the job's
`summary-distinct.json`; misses are reported the same way as hits.

## What was measured

80 measured pairs of public PX4 logs (40 "null" pairs: same vehicle, same build, no setting changed, at most 7 days apart and
different durations; 40 "config" pairs where a setting or the firmware changed). 60 more candidate pairs could not be measured
(a log missing, too large or unreadable). No pair was byte-identical this time (D1: 0 of 80), so the duplicate problem of the
first study is gone.

## Checks on the data

| Check | Rule | Result |
|---|---|---|
| G1 gate | at least 30 distinct null pairs | 40: passed |
| D1 duplicates | at most 2 identical pairs among the measured | 0 of 80: held |
| D2 equal durations | 25-55% of first-study pairs have equal integer durations | 2,402 of 6,136 vehicles (39.2%): held |
| D3 equal duration means same file | at least 18 of 20 equal-duration pairs byte-identical | 18 of 20: held |

## Predictions (share of null pairs on which an item fires)

| Item | Limit | n | Fired | Result |
|---|---|---|---|---|
| P1 GPS lag | at most 10% | 13 | 3 (23.1%; all three "better"; median absolute change 13.1 ms) | **failed** |
| P2 vibration | at most 15% | 37 | 2 (5.4%) | held |
| P3 motor saturation | at most 10% | 30 | 3 (10.0%) | held (at the limit) |
| P4 compass coupling | at most 25% | 13 | 2 (15.4%) | held |
| P5 battery resistance | needs 10 | 6 | 0 | too few |
| P6 any item worse | at most 30% | 40 | 13 (32.5%) | **failed** |
| P7 settings changed (config share) | 20-60% | 80 | 40 (50.0%) | held |

## What it means

- The comparison is noisier than planned on two counts. GPS lag moved past its rule in 3 of 13 unchanged pairs, and 13 of 40
  unchanged pairs had at least one item marked "worse". The items behind most "worse" marks were clipping per minute
  (8 worse of 37) and satellites used (5 worse of 30). Both depend on where and how the vehicle flew, not only on its health.
- So a single "worse" item between two flights of an unchanged drone is common (about one pair in three) and is not evidence
  of a fault by itself. Pooled with the 18 distinct null pairs of the first study, 17 of 58 null pairs (29%) had a worse item.
- Rules that held: vibration, motor saturation and compass coupling rarely fire on an unchanged vehicle.

## Limits

Small n for the GPS items (13) and battery (6). Uploaded logs are not a random sample of flights. This closes the compare-2026
study; no further runs are planned.

MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name; no human has checked these numbers.
Text CC BY 4.0.

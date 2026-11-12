# compare-2026, part 2: false alarms on pairs of distinct flights

Pre-registered 2026-11-12, after the first compare-2026 job was graded ([GRADE.md](GRADE.md)) and before any
log in this part is read. By MV2 of Munim, Inc.; made with AI agents working in his name. Code Apache-2.0, text
and data CC BY 4.0.

## Why

The first job met its gate by the letter of [PLAN.md](PLAN.md) (40 null pairs), but 22 of those 40 pairs were
the same ULog file uploaded twice to Flight Review (identical sha256). On an identical file no rule can fire,
so those pairs say nothing about flight-to-flight noise. PLAN.md did not exclude them. That was our mistake.
On the 18 pairs of distinct flights, the study is a case series (fewer than 30 pairs), and one rule fired
above its limit (vibration, 3 of 16). This part repeats the study on distinct flights, so the false-alarm rates
the compare mode reports rest on what they claim to measure.

## What stays the same

- `compare.py` 0.1.0, `test_compare.py` and `pairs.py` 0.1.0 exactly as the first job ran them: the job
  downloads them from commit 6ded898 and checks each sha256 against that job's `job-fetched.json`.
- px4-log-health checkup 0.3.9, its pinned dependencies, the rules C1 to C8 and E1, E2, and every threshold.
- Log eligibility (`pairs.compact`: real hardware, multicopter, PX4 v1.13 or newer, 180 s or longer, real
  vehicle id), same-build pairing (release, git hash, hardware, airframe), at most 7 days apart, 50 MB cap,
  6 s between downloads, null = both logs read and no setting differs, config = some setting differs.

## What changes

`pairs_distinct.py` 0.1.0 (this folder):

1. **Fresh vehicles only.** Vehicles after `02fa996ad548`, the last vehicle the first job reached, in the
   same sha256 order, minus the 11 upgrade-2026 vehicles.
2. **Duration filter.** For each vehicle, the most recent adjacent same-build pair whose two logs have
   different durations in Flight Review's listing (`duration_s`, whole seconds). An identical file always
   has the same duration, so this skips re-uploads before downloading. It also skips the rare distinct pair
   whose durations match to the second; the count of skipped pairs is kept per row.
3. **Backstop.** A pair whose two files are still byte-identical is set aside as `duplicate`, never counted as
   null or config.
4. **Caps.** 40 distinct null pairs, 400 downloads or 240 minutes, whichever comes first.

## Predictions

On the distinct null pairs of this part alone, with the same thresholds and minimum counts as PLAN.md:

| # | Prediction | Needs |
|---|---|---|
| G1 | Gate: at least 30 distinct null pairs. Below that, this part is a case series too. | |
| P1 | GPS lag: median absolute change 10 ms or less, and C1 fires on 10% or fewer. | 12 pairs |
| P2 | Vibration: C3 fires on 15% or fewer. | 12 pairs |
| P3 | Motor saturation: C5 fires on 10% or fewer. | 12 pairs |
| P4 | Compass: C6 fires on 25% or fewer. | 10 pairs |
| P5 | Battery: C7 fires on 10% or fewer (reading as in PLAN.md). | 10 pairs |
| P6 | At least one vehicle item is `worse` on 30% or fewer. | 30 pairs |
| P7 | Among distinct pairs with both logs read, some setting differs on 20% to 60%. | 30 pairs |

P2 is the one we expect may fail: in the first job's distinct pairs it fired on 3 of 16. We keep the limit.

On duplicates (new):

| # | Prediction | Needs |
|---|---|---|
| D1 | With the duration filter, at most 2 measured pairs are byte-identical. | 30 measured pairs |
| D2 | Over every vehicle in Flight Review's public listing that has a first-study pair (no downloads), that pair's two logs have the same duration on 25% to 55% of vehicles. In the first job, 29 of 71 non-upgrade pairs with both files read (41%) were byte-identical. | 100 vehicles |
| D3 | For the first 20 fresh vehicles whose first-study pair has equal durations and both files download, at least 18 pairs are byte-identical. This checks that D2 counts re-uploads, not coincidences. | 20 pairs |

**Described, not predicted:** the 50th, 90th and 95th percentiles of each change; config pairs; the pooled
result over the first job's 18 distinct null pairs and this part's (labelled as pooled, never as the test).

## What follows

- Whatever comes out, the compare mode's report quotes the false-alarm rates from distinct pairs, with their
  counts, and never the first job's figures that include duplicates.
- If a prediction fails because a rule fires too often, the next compare.py raises that rule's threshold to
  this part's null 95th percentile (never lowers it), and that version is checked on further fresh vehicles
  before it ships with a claim.
- If D2 and D3 hold, a large share of same-build back-to-back uploads in Flight Review are re-uploads of one
  file. Anyone mining public PX4 logs (including MV2's own censuses) should drop byte-identical files first.
  We will check our earlier census studies for this and report what changes.
- Rows (`rows-distinct.jsonl`, `validation-d3.jsonl`), `summary-distinct.json` and a GRADE-distinct.md go in
  this folder.

## Limits, stated in advance

All the limits in PLAN.md still apply. The duration filter can drop a distinct pair whose durations match to
the second. A re-upload that was trimmed or re-encoded is a different file and is not caught. Vehicles later
in hash order are not a different population, but they are not the same vehicles, so differences from the
first job may be chance.

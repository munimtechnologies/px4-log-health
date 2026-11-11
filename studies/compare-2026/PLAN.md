# compare-2026: how often a before/after comparison of two PX4 logs fires when nothing changed

Pre-registered 2026-11-11, before any log in this study was read. By MV2 of Munim, Inc.; made with AI agents
working in his name. Code Apache-2.0, text and data CC BY 4.0.

## Why

Owners who change something on a PX4 vehicle (firmware, GPS receiver, u-blox dynamic model, parameters, props)
want to know whether the next flight got measurably worse. On the PX4 forum (discuss.px4.io topic 49705, posts #9
and #11), the forum user yigitcan-ozturk suggested a narrow before-and-after validation on two comparable logs of one aircraft,
with pre-registered acceptance checks for GPS delay mismatch, innovation consistency, configuration changes and
missing or non-comparable data, without claiming that a pass shows the vehicle is safe.

Any such check is only as good as its false-alarm rate. Two flights of one vehicle differ even when nothing was
changed: wind, payload, battery, how they were flown. We found no published measure of how much PX4 log-health
figures move between two flights of the same vehicle with the same firmware and settings (search of the PX4
docs, forum, GitHub and the web, 2026-11-11: there are tools to diff parameters, such as pyulog's ulog_params,
but no repeatability figures and no before/after tool with stated thresholds). This study measures that noise
floor on public logs, for the rules in `compare.py` 0.1.0, which are fixed here before any data is read.

## The tool

`compare.py` 0.1.0 (repo root) runs px4-log-health checkup 0.3.9 on both logs and compares them item by item.
Each item is `same`, `better`, `worse`, `changed` (moved, direction undefined) or `not comparable` (missing on
one log). A rule "fires" when the item is `better`, `worse` or `changed`.

| Rule | Item | Fires when |
|---|---|---|
| C1 | `gps_lag_ms` (gps_lag.py) | the lag moves by more than 30 ms and more than 2 combined standard errors; worse/better by whether it moved away from or toward the delay EKF2 assumed |
| C1b | `gps_delay_status` | checkup's GPS status flips between ok (within 50 ms of the setting) and off |
| C2 | `gps_innov_excess_ms` (innov.py) | the size of the delay excess implied by EKF2's GPS velocity innovations moves by more than 30 ms and 2 combined SE |
| C3 | `vibration_median` | the in-flight median rises by half (or falls by a third) and by at least 0.5 m/s^2; not comparable if either log uses PX4 v1.12-or-older units |
| C4 | `clipping_per_min` | clipping starts or stops, or its rate changes 3-fold |
| C5 | `motor_saturation_share` | the share of flight with a motor at full command moves by 5 percentage points or more |
| C6 | `compass_coupling` | compass.py's class (weak, moderate, strong) changes |
| C7 | `battery_r_collapse` | PX4's battery-resistance estimate collapses to 0 on one flight and not the other (battery_r.py) |
| C8 | `battery_warning`, `failsafe_in_flight` | an in-flight battery warning or failsafe appears or goes away |
| E1, E2 | `satellites_median`, `eph_median` | satellites move by 3 or more, eph 1.5-fold; environment items, listed but never counted against the vehicle |

It also lists every parameter that differs between the two logs, split into settings, values PX4 learns and saves
itself (`CAL_*` sensor offsets and temperatures, `EKF2_MAG_DECL`, `SENS_DPRES_OFF`) and counters
(`LND_FLIGHT_T_*`, `COM_FLIGHT_UUID`).

## Data

`pairs.py` 0.1.0 (this folder) does everything below in a sealed container. It runs `test_compare.py` and its own
self-test first, then checks that this plan, `pairs.py`, `compare.py` and `test_compare.py` on main are the copies
it runs, before it reads any log.

- **Null pairs.** It streams review.px4.io/dbinfo and keeps public logs that are not SITL, simulation or CI, have a
  vehicle id (not a placeholder), last 180 s or more, are multicopters, and run PX4 v1.13 or newer. For each
  vehicle, in order of the sha256 of its id, it takes the most recent two adjacent logs (by upload date, then log
  id) with the same `ver_sw_release`, `ver_sw` (git hash, when both have one), `sys_hw` and `sys_autostart_id`,
  uploaded at most 7 days apart. One pair per vehicle. It downloads both logs (50 MB or less), runs checkup 0.3.9
  and compare.py, and deletes the logs. A pair is **null** when both logs are read (checkup's health section needs
  60 s in the air) and no setting differs; **config** when both are read and some setting differs. It stops at 40
  null pairs, 220 downloads or 200 minutes, whichever comes first.
- **Upgrade pairs.** The 11 vehicles of px4-gps-lag `studies/upgrade-2026` (v1/pairs.jsonl and
  ext/pairs-ext.jsonl): one log on PX4 v1.16 and one on v1.17, 9 of which also switched from u-blox dynamic model
  6 to 7. Their GPS lag has been published, so for GPS lag they are a reproduction check, not a prediction.

"Before" and "after" in a null pair are upload order, which is not always flight order; every rule is read
for firing in either direction.

## Predictions (on null pairs)

| # | Prediction | Needs |
|---|---|---|
| G1 | Gate: at least 30 null pairs. Below that, the study is a case series and makes no claim. | |
| P1 | GPS lag: the median absolute change is 10 ms or less, and C1 fires on 10% or fewer. | 12 pairs with both lags measured |
| P2 | Vibration: C3 fires on 15% or fewer. | 12 comparable pairs |
| P3 | Motor saturation: C5 fires on 10% or fewer. | 12 comparable pairs |
| P4 | Compass: C6 fires (class changes) on 25% or fewer. | 10 comparable pairs |
| P5 | Battery: C7 fires (collapse on one flight only) on 10% or fewer. | 10 comparable pairs |
| P6 | At least one vehicle item is `worse` on 30% or fewer of null pairs. | 30 null pairs |
| P7 | Among pairs with both logs read, some setting differs on 20% to 60%. | 30 pairs |

Reading P5: the battery-r-2026 census found a collapse on 13-14% of flights. If a collapse were a fresh accident
on every boot, two flights would disagree about 2 x 0.135 x 0.865 = 23% of the time. At 10% or less, a collapse
is mostly a property of the vehicle (its current sensing or its settings). Between 10% and 20% we say it is
unclear. Above 20%, it behaves like a per-boot accident. Whatever comes out is reported.

**Check U1 (upgrade pairs, not a prediction):** on at least 10 of the 11 upgrade pairs, both lags are measured
and compare.py's lag change is within 5 ms of the published change (post minus pre). gps_lag.py 0.2.0 is not the
version upgrade-2026 used, so small differences are expected.

**Described, not predicted:** the 50th, 90th and 95th percentiles of each change on null pairs (the size a change
must exceed to stand out from 95% of nothing-changed pairs); every item on config pairs and upgrade pairs; how
many settings change between two flights.

## What follows from the result

- If a prediction fails because a rule fires too often, compare.py 0.2.0 raises that rule's threshold to the null
  95th percentile (never lowers it), and that version is checked on a fresh set of null pairs (vehicles after the
  last one used here in hash order) before it ships with the claim.
- The compare mode ships in px4-log-health with these rules and this study's measured false-alarm rates in its
  report, whatever they are.
- The rows (`pairs-rows.jsonl`), `summary.json` and a GRADE.md go in this folder.

## Limits, stated in advance

Public uploads are not a random sample of PX4 flights. Upload order is not flight order. Same firmware and
settings do not mean nothing changed (props, payload, battery, weather and the pilot's flying are not in the
log's metadata). Log size is capped at 50 MB, which leaves out some long or high-rate logs. A pair where
nothing changed and no rule fired does not show the vehicle is safe; a pair where a rule fired is a reason to
look at both logs in Flight Review, not a diagnosis.

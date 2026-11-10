# battery-r-2026: does PX4's online battery resistance estimate collapse or freeze in flight?

Pre-registered by MV2 of Munim, Inc. (mv2@munimtech.com) on 2026-11-10, before any log is read.
The plan, code and tests were made with AI agents working in MV2's name. Code Apache-2.0, text CC BY 4.0.

## Question

Since [PX4 PR #23205](https://github.com/PX4/PX4-Autopilot/pull/23205) (merged 2024-07-02), PX4's
battery library estimates each pack's internal resistance online, with recursive least squares on
V = V_oc - R * I (forgetting factor 0.95, start value 5 mOhm per cell). When `BAT1_R_INTERNAL` is -1,
the default, the estimate corrects the voltage-based state of charge for the voltage drop under load
([Battery Estimation Tuning](https://docs.px4.io/main/en/config/battery)). Two details of
`Battery::updateInternalResistanceEstimation` (`src/lib/battery/battery.cpp`) matter here:

- an update is accepted only when the covariance norm falls below the norm kept so far, which only
  ever goes down after the reset, so later updates must beat the best norm ever reached;
- a negative estimate is published as zero (`math::max(..., 0.f)`), while the internal state keeps
  the negative value.

A PX4 forum report from 2026-11-07 ([topic 49724](https://discuss.px4.io/t/battery-resistance-estimate-reaches-zero-and-persists-under-load-fmuv6c-bat1-r-internal-1/49724))
saw `battery_status.internal_resistance_estimate` reach zero and stay at zero under load on one
vehicle (FMUv6C, 4S, `BAT1_R_INTERNAL` = -1), with the state of charge dropping under load and
recovering after landing. Its author could not tell whether this is the estimator or the vehicle's
measurements, and posted no logs. We found no published figure for how often the estimate collapses
to zero, or stops changing, across real PX4 flights. This study measures both on public Flight
Review logs. If the estimate is a usable signal, it also gives MV2's free px4-log-health checker a
calibrated battery check.

## Sample

- Flight Review's public list (`https://review.px4.io/dbinfo`), read once.
- Real logs only (no SITL, no simulation, no CI), at least 120 s long, dated 2025-01-01 or later,
  release v1.15 or newer, or no readable release.
- Newest first (ties by log id), one log per vehicle (the first one in that order).
- For each log, only the definitions section is read first. The rest is downloaded (at most
  100 MB) only if the `battery_status` format has the field `internal_resistance_estimate`.
- Limits: 600 logs requested, 150 downloaded, 300 minutes, 6 s between requests, stop at the first
  403/429/444. These limits are part of the plan.
- Only `battery_status`, `vehicle_land_detected` and `vehicle_status` are read. No position is read
  or written out. Each log is deleted after it is read.

## Measure (battr_logs.py 0.1.0)

- **Battery:** the `battery_status` instance whose estimator is running (both
  `internal_resistance_estimate` and `estimation_covariance_norm` present, the norm positive
  somewhere and taking at least two values) with the highest median valid current
  (`current_a >= 0`). Smart and DroneCAN batteries that do not run PX4's estimator are excluded
  this way.
- **Airborne:** `vehicle_land_detected.landed == 0` (held from each sample); `vehicle_status`
  armed state only if a log has no land detector. Samples with invalid current (-1) are dropped.
- **Measurable:** at least 150 airborne samples (30 s at the default 5 Hz) and a median airborne
  current of at least 0.5 A (the estimator only updates while current is positive).
- Per log, over the airborne samples in time order:
  - **collapsed** = the estimate is exactly 0 on every sample of the second half;
  - **froze early** = the estimate's last change comes within the first 25 % of the samples (or it
    never changes in the air);
  - the estimate at the last airborne sample (`r_end`, mOhm per cell), the share of samples at 0,
    the time of the first 0;
  - **step fit** (an independent estimate from the data): over consecutive airborne samples at most
    0.5 s apart with valid voltage, dV = -R x dI by least squares through the origin, divided by the
    cell count (`battery_status.cell_count`, else `BAT1_N_CELLS`), in mOhm per cell. It counts only
    with at least 150 pairs, a 5th-95th percentile current range of at least 5 A, and a Pearson r of
    dV against dI of -0.3 or lower.
  - Also recorded: `BAT1_R_INTERNAL`, `BAT1_N_CELLS`, `BAT1_SOURCE`, `BAT1_CAPACITY`,
    `BAT1_V_EMPTY`, `BAT1_V_CHARGED`, the release and the board.

## Predictions

| | Prediction | Holds if | Minimum |
|---|---|---|---|
| R1 | Collapse to zero is not rare | collapsed / measurable >= 5 % | 20 measurable |
| R2 | The estimate usually freezes early in flight | froze early / measurable >= 50 % | 20 measurable |
| R3 | Where it does not collapse, the estimate is in a plausible range | median `r_end` of non-collapsed logs is between 1 and 20 mOhm per cell | 20 non-collapsed |
| R4 | The estimate does not track the flight's own data | Spearman rho(`r_end`, step fit) < 0.5, over logs with `r_end` > 0 and a valid step fit | 15 such logs |
| R5 | Most vehicles use the estimate | `BAT1_R_INTERNAL` < 0 in >= 80 % of measurable logs that show it | 20 measurable |

A prediction with fewer logs than its minimum is reported as not graded, not as failed.

## What follows

- The grade is written up in `GRADE.md` exactly against this table, with the rows
  (`battr-rows.jsonl`) and the summary (`battr-summary.json`).
- If R1 holds, the rows are offered on the forum topic above as fleet evidence (counts, boards,
  releases, how soon the zero appears), and px4-log-health gets a battery note for logs whose
  estimate collapsed while `BAT1_R_INTERNAL` is -1: the state of charge was then not corrected for
  load. It will not suggest a resistance value.
- If R1 fails, the forum topic gets that answer too: on public logs the collapse is rare.
- Whether to open a PX4 issue is decided only after the grade, and only with log evidence.

## Limits, stated in advance

- `battery_status` is logged at about 5 Hz, while the estimator runs on every battery update, so
  short changes between logged samples are not seen.
- The step fit assumes voltage and current are sampled together and that the pack's resistance
  dominates the voltage steps. Filtering in a power module, wiring resistance and current-sensor
  scale errors bias it. It is a cross-check, not a ground truth.
- A wrong current-sensor scale or sign can itself drive the estimate negative, so a collapse is not
  by itself proof of an estimator fault.
- The estimator restarts at each boot, and a log may start after an earlier flight on the same boot.
- Public Flight Review uploads are not a random sample of PX4 vehicles.

## Files

`battr_logs.py`, `test_battr_logs.py` (synthetic tests, run before any log is read), `job_fetch.py`
(fetches `gnss_logs.py` 0.1.0 from px4-gps-lag's `studies/gnss-2026`, which `battr_logs.py` reuses
for the list and the streaming reader). The census checks that this file on main matches its local
copy before it reads any log.

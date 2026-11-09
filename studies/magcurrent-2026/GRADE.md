# magcurrent-2026: grade

Graded on 2026-11-09 exactly against [PLAN.md](PLAN.md), which was committed before any log was read. The census (`mag_logs.py` 0.1.0, job `px4-log-health-magcurrent-v1`) ran in a sealed container from 14:45 to 15:10 UTC on 2026-11-09. Its synthetic tests passed first, and the plan on `main` matched the job's copy. Rows: [mag-rows.jsonl](mag-rows.jsonl). Scores: [mag-summary.json](mag-summary.json). Fetched code and hashes: [job-fetched.json](job-fetched.json).

**In one line:** on 63 public PX4 flights where it could be measured, the compass field tracked the battery current strongly on 11 (17%). PX4's compass power compensation was switched on in 1 of 144 logs.

## The pass

| | |
|---|---|
| Flight Review list | 488,002 logs listed; 90,141 eligible (real, >= 120 s, 2025-01-01 or later, v1.13+ or no readable release) from 15,101 vehicles |
| Requested | 160 (newest first, one per vehicle); stopped at the plan's limit of 150 downloaded logs |
| Downloaded | 150 (9 more were over 100 MB, 1 had no compass or battery topic) |
| Read OK | 134 (16 had no `vehicle_magnetometer` data) |
| Measurable | 63. Of the 71 not measurable: median airborne current under 0.5 A 24, current range under 5 A 21, no valid battery current 14, fewer than 150 airborne pairs 12 |

## Predictions

| | prediction | holds if | result | grade |
|---|---|---|---|---|
| **K1** | most downloaded logs can be measured | measurable / downloaded >= 50% | 63 / 150 = 42% | **failed** |
| **K2** | the typical vehicle is only mildly affected | median coupling < 5% | median 3.72% | **held** |
| **K3** | strong coupling is not rare | strong / measurable >= 10% | 11 / 63 = 17.5% | **held** |
| **K4** | compass power compensation is almost never on | `CAL_MAG_COMP_TYP` != 0 in < 5% of logs showing it | 1 / 144 = 0.7% | **held** |
| **K5** | coupling shows in the EKF's magnetometer checks | Spearman rho > 0.1 with >= 30 logs having the ratio | only 19 logs had it (rho 0.105) | **not graded** |
| **K6** | internal compasses are worse than external ones | median internal > external, >= 5 each | 0 / 0: no measurable log had `sensor_mag.is_external` for its compass | **not graded** |

"Not graded" means the plan's minimum was not met; it is not the same as failed.

## What the rows show (description, not a grade)

**Coupling % on the 63 measurable flights** (the norm's change over the flight's 5th-95th percentile current range, as % of the median norm):

| p10 | p25 | median | p75 | p90 | max |
|---|---|---|---|---|---|
| 0.51 | 1.27 | 3.72 | 12.96 | 21.56 | 106.0 |

- Classes: weak 46, moderate 6, strong 11. The same fit against the thrust setpoint (61 flights) gives a similar spread: median 4.06%, p90 23.0%.
- **All 11 strong flights used an I2C compass** (52 of the 63 measurable flights did). The 8 flights on DroneCAN compasses had a median of 1.9%, the 52 on I2C 4.2%, and the 2 on SPI 13.7%. The bus does not say whether a compass is internal or external, so this is not a test of K6.
- The strong flights: 6 quadrotors, 2 fixed-wing, 2 tiltrotor VTOLs and 1 octorotor, on Pixhawk 5/5X/6C/6X, MicoAir H743 and ZeroOne X6 boards. None had compensation on.
- The slope was negative on a third of the flights. Current can raise or lower the norm, depending on where the wiring sits relative to the compass.
- The one log with compensation on (`CAL_MAG_COMP_TYP` = 1, thrust-based) measured 7.9% with a weak correlation, so it is classed weak.

## What follows (as the plan set it)

K3 held, so px4-log-health gets a compass check. `compass.py` 0.1.0 uses the census's measurement unchanged and places a log's coupling in this table. On strong coupling it points to PX4's [Compass Power Compensation](https://docs.px4.io/main/en/advanced_config/compass_power_compensation) guide and suggests no parameter values. Before release, a sealed job re-downloads six census logs and checks that `compass.py` gives the census's own figures.

## Limits

- K1 failed: more than half of the downloaded logs could not be measured, mostly because the battery current was missing, flat or too low. The figures describe vehicles that log a working current sensor and fly with at least 5 A of current range.
- A badly calibrated compass also changes its norm with attitude, and hard manoeuvres draw more current. Part of a measured coupling can therefore be calibration error rather than current. Some strong rows (for example a fixed-wing at 98%, r = 0.57) may be that.
- `vehicle_magnetometer` is logged at about 5 Hz, and only instance 0 (the compass the estimator used) was measured.
- K5 and K6 could not be tested with PX4's default logging: `estimator_status.mag_test_ratio` was present for only 19 measurable flights, and no measurable log carried `sensor_mag.is_external` for its compass. We did not check why the ratio was missing on the others.
- Public Flight Review uploads are not a random sample of PX4 vehicles, and 63 flights is a small sample.

MV2 of Munim, Inc. (mv2@munimtech.com). Made with AI agents working in his name. Text CC BY 4.0 (code Apache-2.0).

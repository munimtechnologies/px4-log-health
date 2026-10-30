# fleet-health-2026: plan

Written 2026-10-30, before any log for this study was downloaded. By MV2 of Munim, Inc.; the plan, the code and the write-up are made with AI agents. Text CC BY 4.0. Data: PX4 Flight Review public logs (Dronecode / PX4, CC BY 4.0).

## Question

How often do real PX4 flights uploaded to Flight Review break the limits that Flight Review itself draws, or that the PX4 docs give, for vibration, accelerometer clipping, GPS jamming and spoofing, battery warnings, failsafes and motor saturation? And does a fresh, independent sample give the same GPS lag as [px4-gps-lag's fleet-2026](https://github.com/munimtechnologies/px4-gps-lag/blob/main/studies/fleet-2026/GRADE.md)?

**Why it is new.** Flight Review, PX4's docs and commercial tools (Roboto) check one log at a time. We found no published fleet-wide rate for any of these flags on PX4's public logs, and no published rate of receiver-reported GNSS jamming or spoofing in drone flight logs (web searches on 2026-10-30, plus a read of PX4's docs and Flight Review's plotting code). The closest work characterises single receivers or single test setups.

## The draw

- `fleet_health.py` 0.1.0 reads Flight Review's public log list (`review.px4.io/dbinfo`) with px4-gps-lag `fleet.py` 0.1.1's eligibility rules: real (not SITL, not simulation, not CI) logs of at least 240 s, dated 2025-10-01 or later.
- It leaves out every log and every (hashed) vehicle that fleet-2026 drew (`px4-gps-lag/studies/fleet-2026/v1/logs.jsonl`), so the replication is on new vehicles.
- Vehicles in seed 20261031's random order, **one log per vehicle**, up to **300 logs** or 150 minutes. Downloads are 10 s apart (fleet-2026 used 6 s; this one may run beside another job). Logs over 80 MB are skipped. The run stops at once on HTTP 403 or 444. Each log is deleted as soon as it is measured. No position is read. Vehicle ids are published only as a hash.

## What is measured (health.py 0.1.0), in flight only

"In flight" is wherever the last `vehicle_land_detected` message says not landed. A log is **read** if it has `vehicle_land_detected` and at least 60 s in the air.

| Figure | From | Limit used |
|---|---|---|
| vibration | median of `vehicle_imu_status.accel_vibration_metric`, instance 0 | Flight Review's vibration plot: green < 4.905, orange 4.905-9.81, red > 9.81 m/s² |
| clipping | increase of `vehicle_imu_status.accel_clipping[0..2]` (PX4's running totals), instance 0 | any |
| jamming indicator | median and 95th percentile of `jamming_indicator` (`vehicle_gps_position`, else `sensor_gps`), only where the receiver ever reports a non-zero value | PX4 docs: "should be around or below 40"; "80 or higher" too high |
| jamming state | highest `jamming_state`, only where ever non-zero | 2 (warning) or 3 (critical) |
| spoofing state | highest `spoofing_state`, only where ever non-zero | 2 (indicated) or 3 (multiple) |
| battery | highest `battery_status.warning` (0-4) of instance 0, only where it reads ≥ 1 V | 1 low or worse |
| failsafe | `vehicle_status.failsafe` ever true | any |
| motor saturation | multicopters only (`vehicle_type` 1 throughout, not VTOL), ≥ 4 active motors in `actuator_motors`: share of samples with any motor ≥ 0.99 | ≥ 5% of the flight |

Every log is also measured with px4-gps-lag `gps_lag.py` 0.1.0 unchanged, with its own gates.

## Check and predictions

Each share is over the read logs that have that figure. A prediction needs at least 30 such logs (P4: 10 in each group; R1-R2: 60 logs passing gps_lag's gates); with fewer it is not tested.

| | Stated before the run |
|---|---|
| **E1** | at least 150 logs read, from at least 100 vehicles |
| P1 | 3% to 30% of logs have median vibration orange or red |
| P2 | at most 8% have median vibration red |
| P3 | 5% to 50% of logs clip the accelerometer at least once in flight |
| P4 | logs with orange-or-red vibration clip more often than green ones |
| P5 | at most 20% of logs reporting a jamming indicator have an in-flight median above 40 |
| P6 | at most 15% of logs reporting a jamming state reach warning or critical in flight |
| P7 | at most 10% of logs reporting a spoofing state reach indicated or multiple in flight |
| P8 | 3% to 30% of monitored batteries reach a low (or worse) warning in flight |
| P9 | 3% to 35% of logs enter failsafe in flight |
| P10 | at most 20% of multicopters have a motor at full command in at least 5% of the flight |
| R1 | the median GPS lag of passing logs is 80-120 ms (fleet-2026: 99.8) |
| R2 | 18% to 42% of passing logs differ from their own `EKF2_GPS_DELAY` by over 50 ms (fleet-2026: 29.6%) |

## Decisions, fixed now

- **B1.** If E1 holds, the fleet's quantiles of each figure are published as `baseline.json`, and the next health.py prints where a log sits against them. If E1 fails, no baseline is published from this run.
- **R.** If R1 and R2 both hold, fleet-2026's lag figures count as replicated on an independent sample and px4-gps-lag's README says so. If R1 fails, px4-gps-lag's README adds a caution beside its suggested starting value.
- Every share is reported whether or not its prediction held. Breakdowns not listed here are labelled exploratory.

## Honest limits, known now

- **Not a random sample of PX4 flights.** People upload logs for many reasons, often after a problem, so these rates describe uploaded logs, not the fleet in the air.
- **Instance 0 is not always the IMU that flew the vehicle.** The worst IMU is reported as exploratory.
- **What a receiver reports varies.** Jamming indicators are receiver-specific (u-blox reports a 0-255 CW figure; others report nothing); spoofing and jamming states exist only on some receivers and PX4 versions. Shares are over the logs that report each figure.
- **Land detection lags.** A crash can count as "in flight" for a moment, which can add clipping.
- **The 80 MB cap** drops long, high-rate logs (17% in fleet-2026).

## Run

Job `px4-log-health-fleet-v1` (sealed container): `fetch_deps.py` waits for this file on main before anything is downloaded, then `python -m unittest -v test_health test_gps_lag`, then

```
python fleet_health.py --out-dir /out --exclude v1-logs.jsonl --max-logs 300 --budget-min 150
```

# fleet-health-2026: graded

Graded 2026-10-30 against `PLAN.md`, which was committed (a071284) before any log was downloaded. By MV2 of Munim, Inc.; the plan, code, analysis and this text were made with AI agents. Text CC BY 4.0. Data: PX4 Flight Review public logs (Dronecode / PX4, CC BY 4.0).

Run: job `px4-log-health-fleet-v1`, a sealed container running `health.py` 0.1.0, `fleet_health.py` 0.1.0, and `gps_lag.py` 0.1.0 and `fleet.py` 0.1.1 from px4-gps-lag main (`fetched.json`: every file matched main). The unit tests passed. There was no 403/444. It took 62 minutes. It ran with `--budget-min 140`, where the plan says 150; the budget never came into play, because the 300-log cap stopped the run first.

## The draw

Flight Review listed 483,966 logs. 36,269 were eligible: real flights of at least 240 s from 2025-10-01 on. Excluded were 148,458 shorter logs, 16,326 simulation logs and 282,913 older ones, plus fleet-2026's 400 logs and 337 vehicles. Of 300 logs attempted (one per vehicle), 248 were downloaded (52 were over 80 MB) and **208 were read**, from 208 vehicles. 36 had less than 60 s in the air and 4 had no `vehicle_land_detected`.

## Checks

| | Prediction | Result | Holds |
|---|---|---|---|
| **E1** | ≥ 150 logs read from ≥ 100 vehicles | 208 from 208 | **yes** |
| P1 | 3-30% median vibration orange or red | **40.4%** (80 of 198) | no |
| P2 | ≤ 8% red | **21.7%** (43 of 198) | no |
| P3 | 5-50% clip the accelerometer in flight | 22.7% (45 of 198) | yes |
| P4 | loud logs clip more often than quiet ones | 30.0% (80 logs) vs 17.8% (118) | yes |
| P5 | ≤ 20% of logs reporting a jamming indicator have an in-flight median above 40 | **29.7%** (of 128) | no |
| P6 | ≤ 15% of logs reporting a jamming state reach warning or critical | **70.1%** (54 of 77) | no |
| P7 | ≤ 10% of logs reporting a spoofing state reach indicated or multiple | 8.3% (6 of 72) | yes |
| P8 | 3-30% of monitored batteries reach low or worse | 18.6% (of 177); critical or worse 13.0% | yes |
| P9 | 3-35% of logs enter failsafe in flight | 26.1% (of 207) | yes |
| P10 | ≤ 20% of multicopters have a motor at full command ≥ 5% of the flight | 4.5% (5 of 110) | yes |
| R1 | median GPS lag of passing logs 80-120 ms | 113.8 ms (98 logs, 98 vehicles; IQR 88.5-159.4) | yes |
| R2 | 18-42% differ from their own `EKF2_GPS_DELAY` by > 50 ms | 31.2% (of 93 with the parameter) | yes |

E1 holds. Six of ten health predictions hold, and both replication checks hold.

Of the 208 logs read, **75% raised at least one flag**. By number of flags, the logs split 52 / 63 / 52 / 21 / 16 / 2 for 0 / 1 / 2 / 3 / 4 / 5.

## Decisions, as written

- **B1 (E1 holds):** the fleet's quantiles are published as `baseline.json`. The next `health.py` will print where a log sits against them.
- **R (R1 and R2 hold):** fleet-2026's lag figures count as replicated on an independent sample, and px4-gps-lag's README says so. No caution is added.

## What the misses mean

Our priors were wrong in three places, and two of them come with something worth knowing.

**Vibration (P1, P2), and a unit change in PX4 v1.13.** Uploaded logs vibrate far more than we expected. There is also a catch we did not know when we wrote the plan. Before v1.13, PX4 computed `accel_vibration_metric` from the change in *delta velocity* between IMU samples, in m/s per integration interval (`VehicleIMU.cpp` v1.12.3: `0.01f * delta_velocity_diff.norm()`). From v1.13 it uses the change in *acceleration*, in m/s² (v1.13.3: `Vector3f(acceleration - _acceleration_prev).norm()`). The old figure is roughly 200 times smaller, so Flight Review's 4.905 and 9.81 m/s² bands do not apply to logs from v1.12 or earlier. The data show this plainly (exploratory, counted after the grade):

| PX4 release | logs | median below 0.5 | orange or red | red | clipped in flight |
|---|---|---|---|---|---|
| v1.12 or older | 27 | 26 | 1 | n/a | 7 |
| v1.13 or newer | 136 | 7 | **70 (51.5%)** | **37 (27.2%)** | n/a |
| other version strings (forks, untagged builds) | 35 | n/a | 9 | n/a | n/a |

The old logs are not quiet: 7 of 27 clipped the accelerometer, a share close to the fleet's 22.7%. They only look green because of the unit. So the pre-registered 40.4% *understates* orange-or-red vibration among logs where the bands mean what they say: about half of uploaded flights on v1.13 or later sit in Flight Review's orange or red band. The PX4 docs give no number of their own. They say there is "a broad range between 'everything is ok' and 'the levels are too high'", depending on vehicle size. So the bands are best read as "worth a look", not "unsafe". `health.py` 0.2.0 will not apply the bands to logs older than v1.13.

**Receiver jamming state (P6).** Of 77 logs whose receiver reports a jamming state, 52 peak at 2 and only **2 (2.6%) reach 3**. In the PX4 versions here, state 2 is u-blox's MON-RF "warning: interference visible but fix OK", passed through unchanged. Newer PX4 drivers map state 2 to 3 when the value comes from UBX-SEC-SIG, and note that "commander only alerts on 3". Exploratory: 22 of the 52 state-2 logs (42%) also had a jamming-indicator median above 40, against 13 of 59 (22%) logs whose state stayed at 0-1. So state 2 is common on ordinary flights and only loosely tied to the indicator. `health.py` 0.2.0 will report it as information and flag only state 3.

**Jamming indicator (P5).** 29.7% of logs with an indicator had a median above the docs' "around or below 40", and 17.2% reached 80 or more at the 95th percentile. These are uploaded logs, often uploaded because something went wrong, so this is not the rate in the air.

## GPS lag replication (exploratory detail)

Median lag minus parameter: 0.7 ms. By bus: serial 129.2 ms (46 logs), DroneCAN 107.7 ms (18), unknown 106.2 ms (33), I²C 1 log. DroneCAN again lagged less than serial, as in fleet-2026 (where P4 had predicted the opposite). 95.7% ran the default 110 ms. Logs that did not pass `gps_lag.py`'s gates: 53 where the IMU explained too little of the GPS velocity change, 52 too large to download, 35 with no `vehicle_gps_position`, 30 with too few in-flight GPS pairs, 29 with too little manoeuvring, 2 with the best lag at the edge of the search, and 1 with no `sensor_combined`.

## Limits

- **Uploaded logs are not the fleet.** People often upload a log because something went wrong.
- **One IMU.** Instance 0 is not always the IMU that flew. The worst IMU's median puts 42.4% of logs in orange or red.
- **Receivers differ.** Shares are over the logs that report each figure. Some receivers report −1 or a constant as their jamming indicator (the 10th percentile is −1).
- **Land detection lags.** A crash can count as flight for a moment.
- **Logs over 80 MB were skipped** (52 of 300), which drops long, high-rate flights.
- **The version split was counted after the grade.** It is exploratory, though the unit change it rests on is in PX4's source.

## Files

- `logs.jsonl`: one line per attempted log. Flight Review log ids are public; vehicle ids are hashed. No positions.
- `summary.json`: every share, count and check, and the replication.
- `baseline.json`: quantiles of each figure over the logs read. Its vibration quantiles mix both units (see above). 0.2.0 will rebuild them for v1.13+ only.
- `fetched.json`: hashes of the code the job ran.

# magcurrent-2026: how much does motor current bend the compass on public PX4 flights?

Pre-registered by MV2 of Munim, Inc. (mv2@munimtech.com) on 2026-11-09, before any log is read.
The plan, code and tests were made with AI agents working in MV2's name. Code Apache-2.0, text CC BY 4.0.

## Question

Current in power wiring makes its own magnetic field. A compass close to that wiring sees the
field change with the motor current, and PX4's yaw estimate suffers. PX4's docs
([Compass Power Compensation](https://docs.px4.io/main/en/advanced_config/compass_power_compensation))
and Flight Review's "Thrust and Magnetic Field" plot both say the same thing: the norm of the
measured field should stay constant in flight. If it tracks thrust or current, move the compass,
or fit PX4's compensation (`CAL_MAG_COMP_TYP`, using PX4's own `mag_compensation.py`).

Both tools work one log at a time. We found no published figure for how common this is across
real PX4 flights, or how often the compensation is used. This study measures it on public Flight
Review logs. Its purpose is a calibrated compass check for MV2's free px4-log-health checker,
which has no compass check today.

## Sample

- Flight Review's public list (`https://review.px4.io/dbinfo`), read once.
- Real logs only (no SITL, no simulation, no CI), at least 120 s long, dated 2025-01-01 or later,
  release v1.13 or newer, or no readable release.
- Newest first (ties by log id), one log per vehicle (the first one in that order).
- For each log, only the definitions section is read first. The rest is downloaded (at most
  100 MB) only if the formats have both `vehicle_magnetometer` and `battery_status`.
- Limits: 500 logs requested, 150 downloaded, 300 minutes, 6 s between requests, stop at the
  first 403/429/444. These limits are part of the plan.
- No position is written out. Each log is deleted after it is read.

## Measure (mag_logs.py 0.1.0)

- Field norm: `vehicle_magnetometer` instance 0, `|magnetometer_ga|`. This is the field after
  PX4's calibration and, if enabled, its power compensation.
- Airborne: `vehicle_land_detected.landed == 0` (held from each sample); `vehicle_status`
  armed state only if a log has no land detector.
- Current: the `battery_status` instance with the highest median valid current (`current_a >= 0`),
  interpolated to the compass times; a pair is kept only if a current sample is within 0.5 s.
- Fit over airborne pairs: `norm = a + b * current` (least squares), Pearson r.
- **Coupling % = |b| x (95th - 5th percentile of airborne current) / median norm x 100.**
- A log is **measurable** with at least 150 airborne pairs, a median airborne current of at least
  0.5 A and a 5th-to-95th current range of at least 5 A.
- Classes: **strong** = coupling >= 10 % and |r| >= 0.5; **moderate** = coupling >= 5 % and
  |r| >= 0.3; otherwise **weak**.
- Also recorded: the compass's device id and bus, whether `sensor_mag` marks it external,
  `CAL_MAG_COMP_TYP`, `EKF2_MAG_TYPE`, the median airborne `estimator_status.mag_test_ratio`,
  and the same fit against the thrust setpoint (reported, not graded).

## Predictions

| | Prediction | Holds if |
|---|---|---|
| K1 | Most downloaded logs can be measured | measurable / downloaded >= 50 % |
| K2 | The typical vehicle is only mildly affected | median coupling < 5 % |
| K3 | Strong coupling is not rare | strong / measurable >= 10 % |
| K4 | Compass power compensation is almost never on | logs with `CAL_MAG_COMP_TYP` != 0 / logs whose head shows the parameter < 5 % |
| K5 | Coupling shows up in the EKF's magnetometer checks | Spearman rho(coupling, median airborne mag_test_ratio) > 0.1, with at least 30 measurable logs having the ratio |
| K6 | Internal compasses are worse than external ones | median coupling of internal > of external, with at least 5 in each group |

A prediction with too few logs is reported as not graded, not as failed.

## What follows

- The grade is written up in `GRADE.md` exactly against this table, with the rows
  (`mag-rows.jsonl`) and the summary (`mag-summary.json`).
- If the coupling measure separates vehicles (K3 holds, or the spread is wide), px4-log-health
  gets a compass check: the log's coupling %, its rank in this fleet, and a pointer to PX4's
  compass power compensation guide. It will not suggest parameter values.

## Limits, stated in advance

- The norm also changes with attitude when the calibration is imperfect, and hard manoeuvres draw
  more current, so part of a measured coupling can be calibration error rather than current.
- A log that already has good compensation shows little coupling by design. That is reported,
  not corrected for.
- Public Flight Review uploads are not a random sample of PX4 vehicles.
- `vehicle_magnetometer` is logged at 5 Hz by default, so fast changes are not seen.
- Vehicles without a current sensor cannot be measured (K1 measures how many).

## Files

`mag_logs.py`, `test_mag_logs.py` (synthetic tests, run before any log is read), `job_fetch.py`
(fetches `gnss_logs.py` 0.1.0 from px4-gps-lag's `studies/gnss-2026`, which `mag_logs.py` reuses
for the list and the streaming reader). The census checks that this file on main matches its
local copy before it reads any log.

---
name: px4-log-health
description: Use when a user has a PX4 flight log (.ulg) and asks whether the flight was healthy, or asks about vibration, accelerometer clipping, GPS jamming or spoofing, battery warnings, a state of charge that drops under load and recovers after landing (BAT1_R_INTERNAL, PX4's battery internal-resistance estimate), failsafe, motor saturation, compass interference from motor current (CAL_MAG_COMP_TYP, compass power compensation), GPS delay (EKF2_GPS_DELAY, SENS_GPS0_DELAY, SENS_GNSS0_DELAY, DroneCAN GPS timing) or the u-blox GPS_UBX_DYNMODEL setting. Runs the open-source px4-log-health checkup, bundled in this skill's scripts/ folder, on the user's own machine (nothing is uploaded, no position is read) and explains its report - Flight Review's vibration bands, each figure's place among public flights, the GPS lag measured from the flight against the delay EKF2 applied, how much the compass follows battery current, and whether PX4's battery-resistance estimate collapsed to zero. PX4 only; not for ArduPilot .bin/.log files.
license: Apache-2.0
---

# PX4 log health

Made with AI agents working in MV2's (Munim, Inc.) name. Skill and checker: Apache-2.0. Source,
studies and every figure quoted here: https://github.com/munimtechnologies/px4-log-health and
https://github.com/munimtechnologies/px4-gps-lag .

`scripts/checkup.py` reads one or more PX4 flight logs (ULog, `.ulg`) and writes a short Markdown
report per log, plus one JSON line per log. For the time the vehicle was in the air it reports:

- **vibration** (`vehicle_imu_status.accel_vibration_metric`) against the bands Flight Review
  draws: green below 4.905 m/s², orange to 9.81, red above. PX4 v1.12 and older log this
  metric in other units, so no band is given for them;
- **accelerometer clipping**, the **GNSS jamming indicator** (PX4 docs: "around or below 40"
  is fine, 80 or more is too high), the receiver's jamming and spoofing states, satellites and
  eph; the worst **battery warning**; whether it entered **failsafe**; **motor saturation** on
  multicopters;
- where each figure sits among **208 public PX4 flights** (e.g. `p75-p90`, `above p99`);
- a **GPS delay verdict**: how far the GPS lags the IMU, measured from the flight with
  px4-gps-lag, against the delay EKF2 applied, cross-checked with EKF2's own GPS velocity
  innovations;
- a **note on u-blox dynamic model 6** when a serial u-blox receiver runs
  `GPS_UBX_DYNMODEL` = 6;
- a **compass check**: how much the magnetic field strength of the compass the estimator used
  follows the battery current in flight, placed among 63 measurable public PX4 flights;
- a **battery check**: whether PX4's online battery-resistance estimate collapsed to 0 in flight,
  and whether PX4 was using it to correct the state of charge for load.

## When to use

- The user shares or names a `.ulg` file and asks "was this flight OK?", "why did it drift /
  toilet-bowl / reject GPS?", "is my vibration too high?", "is my compass affected by the
  motors?", "why does my battery percentage drop under load and come back after landing?", or
  similar.
- The user asks what `EKF2_GPS_DELAY` / `SENS_GPS0_DELAY` / `SENS_GNSS0_DELAY` should be for
  their vehicle, whether their DroneCAN GPS is timed right, or whether to change
  `GPS_UBX_DYNMODEL`.
- A repository holds PX4 test-flight logs and the user wants a check in CI (see the GitHub
  Action below).

Do not use it for ArduPilot DataFlash logs (`.bin`, `.log`), Betaflight/INAV blackbox logs or
MAVLink `.tlog` files: it reads ULog only.

If the user would rather not install anything, point them to the browser version, which runs
the checker in the tab with nothing uploaded: https://munimv2.github.io/px4-log-check/

## Step 1: install the two Python packages it needs

The checker itself is already here, in this skill's `scripts/` folder (next to this SKILL.md):
`checkup.py`, `health.py`, `compass.py`, `battery_r.py` and `baseline.json` from px4-log-health
v0.3.9, and `gps_lag.py` and `innov.py` from px4-gps-lag 0.2.0. `scripts/SOURCES.json` lists where
each file came from and its sha256. Nothing else is downloaded. It needs Python 3 and two packages
from PyPI; use a virtual environment if the user prefers.

```
pip install numpy pyulog
```

## Step 2: run it on the log

`scripts/` below is this skill's folder; when you run from the user's project, use its full path
(for example `.claude/skills/px4-log-health/scripts/checkup.py`).

```
python scripts/checkup.py path/to/flight.ulg --md report.md --json rows.jsonl
```

- Several logs at once: `python scripts/checkup.py a.ulg b.ulg c.ulg --md report.md --json rows.jsonl`.
- Without `--md` the report goes to stdout. `--md` appends to an existing file.
- `--fail-on-flag` makes the exit code 1 when any log raises a flag (a compass flag included) or
  gets a suggested GPS delay (for CI). Notes, such as the battery note, don't change the exit
  code.
- The checker reads the log locally and reads no position; nothing is sent anywhere.

Then read `report.md` and summarise it for the user. Use `rows.jsonl` when you need exact
numbers.

## Step 3: explain the report honestly

**Health.** Each flag is a reason to look at that part of the log in Flight Review
(https://review.px4.io/) or PlotJuggler, **not a diagnosis**. Say which figure raised it and
where it sits among the 208 public flights. Context from the same 208 flights: 75% raised at
least one flag, and among logs from PX4 v1.13 or newer 51% sat in Flight Review's orange or
red vibration band. These are uploaded logs, often uploaded because something went wrong, so
a flag is common and not alarming on its own. If the report says "Health not read: under 60 s
in the air", the flight was too short for the health figures; the GPS delay may still be
measured.

**GPS delay.** The JSON field `gps_verdict` holds the verdict:

- `lag_ms` / `se_ms`: the measured lag of GPS velocity behind IMU-derived velocity;
- `param_ms`: the delay EKF2 applied. Up to PX4 v1.17 that is `EKF2_GPS_DELAY`. From v1.18 it
  is read from the log's own sample times, and `param_name` names the receiver's slot
  (`SENS_GPS0_DELAY` / `SENS_GPS1_DELAY` on v1.18, `SENS_GNSS0_DELAY` / `SENS_GNSS1_DELAY` on PX4
  main);
- `delay_set_by` (v1.18 and newer): `parameter`, `DroneCAN receiver` or `PPS or GPS driver`;
- `d_ms`: lag minus applied delay; `innov`: whether EKF2's own innovations agree;
- `status`: `ok` (within 50 ms) or `off` (more than 50 ms off); `near_line` is true when it is
  40 to 50 ms off;
- `suggest_ms`: a suggested starting value, given only when the lag is more than 50 ms off,
  the innovations don't point the other way, **and** a parameter set the delay.

Present a suggested value as **a starting point to verify**, never as a setting to fly: measure
two or three flights first, and check the change with EKF2 log replay before flying it. About
30% of public PX4 vehicles were more than 50 ms off their setting. Hover-only flights usually
can't give a lag (the measurement needs some manoeuvring); the report then says why
(`gps.lag.reason`).

**DroneCAN receivers.** When `delay_set_by` is `DroneCAN receiver`, PX4 used the receiver's own
time stamps (PX4 PR #27427, in v1.18 and main) and did not apply the delay parameter, so tell the
user that changing `SENS_GPSn_DELAY` / `SENS_GNSSn_DELAY` would not change anything for that
receiver. The same holds for `PPS or GPS driver`. If such a log is more than 50 ms off, the report
gives no value to try; suggest looking at the flight in EKF2 log replay and checking the
receiver's own timing settings with its maker. Whether DroneCAN receivers' self-reported delay
usually covers their lag is open: px4-gps-lag's pre-registered tests found too few public logs
to grade it (`studies/dronecan-2026/GRADE-ext.md`, `GRADE-nodefw.md`), so don't claim it does or
doesn't.

**u-blox dynamic model 6.** When the report ends its GPS section with "u-blox dynamic model
6", the receiver runs `GPS_UBX_DYNMODEL` = 6 (airborne <1g), PX4's multicopter default up to
v1.16 (v1.17 moved multicopters to 7). On public logs model 6 lagged a median 46 ms more than
model 7, and the extra lag behaved like heavier smoothing, which no single delay setting fully
cancels. The report suggests trying model 7 (it takes effect after a reboot) and measuring
again. It is a note, not a flag. In the JSON it is `gps_dynmodel` (`value`, `note`).

**Compass.** The Compass section, and the JSON field `compass` (`level`, `coupling_pct`,
`class`, `fleet_place`, `cal_mag_comp_typ`, `measure`), says how far the field strength of the
compass the estimator used moved over the flight's battery current range, as a percentage, and
where that sits among 63 measurable public PX4 flights (median 3.7%, 90th percentile 21.6%):

- `flag` (strong: 10% or more with |r| 0.5 or more; 17% of those flights): motor current is
  likely bending the compass. Point the user to PX4's Compass Power Compensation guide
  (https://docs.px4.io/main/en/advanced_config/compass_power_compensation) or to moving the
  compass away from power wiring. Give no parameter values.
- `note` (moderate): worth a look at Flight Review's "Thrust and Magnetic Field" plot.
- `ok`: the compass barely follows the current.
- `not checked`: most often the log's battery current is missing, flat or under 5 A of range.
  That was so for more than half of public logs, and it is not a fault.

Say that a badly calibrated compass also changes its strength with attitude, and hard
manoeuvres draw more current, so a strong coupling can partly be calibration error; a compass
recalibration and a look at the plot come before any hardware change.

**Battery.** The last section, and the JSON field `battery` (`level`, `collapsed`,
`r_end_mohm`, `bat_r_internal`, `measure`), says whether PX4's online battery-resistance
estimate (`battery_status.internal_resistance_estimate`) was 0 for the whole second half of the
flight:

- `note`: it collapsed while `BAT1_R_INTERNAL` is -1, so PX4 did not correct the voltage-based
  state of charge for load; the percentage reads low under load and recovers after landing. On
  public PX4 flights this happened on 9 of 53 that use the estimate, and in most of them the
  estimate was already 0 at takeoff. Tell the user it can also come from a wrong current-sensor
  scale or offset, and point them to PX4's battery estimation guide
  (https://docs.px4.io/main/en/config/battery) and to the battery plots in Flight Review. Do not
  suggest a resistance value.
- `ok`: the estimate did not collapse (its final value is given against public flights: median
  2.7 mOhm per cell), or it collapsed but `BAT1_R_INTERNAL` is set, so PX4 used the set value.
- `not checked`: no running estimator (smart or DroneCAN batteries, or PX4 older than v1.15), no
  valid current, or under 30 s in the air.

Never tell the user a vehicle is safe or unsafe to fly on the strength of this report.

## In CI (GitHub Action)

```yaml
- uses: actions/checkout@v4
- uses: munimtechnologies/px4-log-health@v0.3.9
  with:
    logs: "**/*.ulg"          # space-separated globs
    fail-on-flag: "false"     # "true" fails the step on any flag or suggestion
```

The report lands in the job summary, and one JSON line per log in `px4-log-health.jsonl`.

## Sources and limits

- Fleet figures: 208 public Flight Review logs, one per vehicle, uploaded from October 2025
  (`studies/fleet-health-2026/GRADE.md`, pre-registered).
- Compass figures: 63 measurable of 150 public Flight Review logs, one per vehicle
  (`studies/magcurrent-2026/GRADE.md`, pre-registered).
- Battery figures: 75 measurable of 150 public Flight Review logs, one per vehicle
  (`studies/battery-r-2026/GRADE.md`, pre-registered).
- GPS lag method and fleet figures: px4-gps-lag (`studies/fleet-2026`,
  `studies/mismatch-2026`, `studies/dynmodel-2026`, `studies/gnss-2026`), each pre-registered
  before data was read.
- Vibration bands from Flight Review; jamming limits from the PX4 docs.
- Nobody at PX4 or Dronecode has reviewed these results. Report problems or questions at
  https://github.com/munimtechnologies/px4-log-health/issues .

MV2, Munim, Inc., mv2@munimtech.com

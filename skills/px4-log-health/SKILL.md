---
name: px4-log-health
description: Use when a user has a PX4 flight log (.ulg) and asks whether the flight was healthy, or asks about vibration, accelerometer clipping, GPS jamming or spoofing, battery warnings, failsafe, motor saturation, GPS delay (EKF2_GPS_DELAY, SENS_GPS0_DELAY, SENS_GNSS0_DELAY) or the u-blox GPS_UBX_DYNMODEL setting. Runs the open-source px4-log-health checkup on the user's own machine (nothing is uploaded, no position is read) and explains its report - Flight Review's vibration bands, each figure's place among 208 public flights, and the GPS lag measured from the flight against the delay EKF2 assumed. PX4 only; not for ArduPilot .bin/.log files.
license: Apache-2.0
---

# PX4 log health

Made with AI agents working in MV2's (Munim, Inc.) name. Skill and checker: Apache-2.0. Source,
studies and every figure quoted here: https://github.com/munimtechnologies/px4-log-health and
https://github.com/munimtechnologies/px4-gps-lag .

`checkup.py` reads one or more PX4 flight logs (ULog, `.ulg`) and writes a short Markdown report
per log, plus one JSON line per log. For the time the vehicle was in the air it reports:

- **vibration** (`vehicle_imu_status.accel_vibration_metric`) against the bands Flight Review
  draws: green below 4.905 m/s², orange to 9.81, red above. PX4 v1.12 and older log this
  metric in other units, so no band is given for them;
- **accelerometer clipping**, the **GNSS jamming indicator** (PX4 docs: "around or below 40"
  is fine, 80 or more is too high), the receiver's jamming and spoofing states, satellites and
  eph; the worst **battery warning**; whether it entered **failsafe**; **motor saturation** on
  multicopters;
- where each figure sits among **208 public PX4 flights** (e.g. `p75-p90`, `above p99`);
- a **GPS delay verdict**: how far the GPS lags the IMU, measured from the flight with
  px4-gps-lag, against the delay EKF2 assumed, cross-checked with EKF2's own GPS velocity
  innovations;
- a **note on u-blox dynamic model 6** when a serial u-blox receiver runs
  `GPS_UBX_DYNMODEL` = 6.

## When to use

- The user shares or names a `.ulg` file and asks "was this flight OK?", "why did it drift /
  toilet-bowl / reject GPS?", "is my vibration too high?", or similar.
- The user asks what `EKF2_GPS_DELAY` / `SENS_GPS0_DELAY` should be for their vehicle, or
  whether to change `GPS_UBX_DYNMODEL`.
- A repository holds PX4 test-flight logs and the user wants a check in CI (see the GitHub
  Action below).

Do not use it for ArduPilot DataFlash logs (`.bin`, `.log`), Betaflight/INAV blackbox logs or
MAVLink `.tlog` files: it reads ULog only.

If the user would rather not install anything, point them to the browser version, which runs
the same `checkup.py` in the tab with nothing uploaded:
https://munimv2.github.io/px4-log-check/

## Step 1: get the checker (pinned release)

Needs Python 3 and network access for this step only. Use a fresh folder or a virtual
environment.

```
git clone --depth 1 --branch v0.3.6 https://github.com/munimtechnologies/px4-log-health
cd px4-log-health
pip install numpy pyulog requests
python fetch_deps.py
```

Without git, download and unpack
https://github.com/munimtechnologies/px4-log-health/archive/refs/tags/v0.3.6.zip instead.
`fetch_deps.py` downloads `gps_lag.py`, `innov.py`, `fleet.py` and `test_gps_lag.py` from
px4-gps-lag at a pinned commit and checks each file's version. Check for a newer release at
https://github.com/munimtechnologies/px4-log-health/releases and use its tag if there is one.

## Step 2: run it on the log

```
python checkup.py path/to/flight.ulg --md report.md --json rows.jsonl
```

- Several logs at once: `python checkup.py a.ulg b.ulg c.ulg --md report.md --json rows.jsonl`.
- Without `--md` the report goes to stdout. `--md` appends to an existing file.
- `--fail-on-flag` makes the exit code 1 when any log raises a flag or gets a suggested GPS
  delay (for CI). Without it, flags and suggestions don't change the exit code.
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
- `param_ms` and `param_name`: the delay EKF2 assumed and the parameter behind it
  (`EKF2_GPS_DELAY` up to PX4 v1.17, `SENS_GPS0_DELAY` / `SENS_GPS1_DELAY` from v1.18,
  `SENS_GNSS0_DELAY` on PX4 main);
- `d_ms`: lag minus setting; `innov`: whether EKF2's own innovations agree;
- `status`: `ok` (within 50 ms) or `off` (more than 50 ms off); `near_line` is true when it is
  40 to 50 ms off;
- `suggest_ms`: a suggested starting value, given only when the lag is more than 50 ms off
  **and** the innovations don't point the other way.

Present a suggested value as **a starting point to verify**, never as a setting to fly: measure
two or three flights first, and check the change with EKF2 log replay before flying it. About
30% of public PX4 vehicles were more than 50 ms off their setting. Hover-only flights usually
can't give a lag (the measurement needs some manoeuvring); the report then says why
(`gps.lag.reason`).

**u-blox dynamic model 6.** When the report ends its GPS section with "u-blox dynamic model
6", the receiver runs `GPS_UBX_DYNMODEL` = 6 (airborne <1g), PX4's multicopter default up to
v1.16 (v1.17 moved multicopters to 7). On public logs model 6 lagged a median 46 ms more than
model 7, and the extra lag behaved like heavier smoothing, which no single delay setting fully
cancels. The report suggests trying model 7 (it takes effect after a reboot) and measuring
again. It is a note, not a flag. In the JSON it is `gps_dynmodel` (`value`, `note`).

**PX4 main logs.** On logs from current PX4 main (topic `vehicle_gnss`, parameters
`SENS_GNSSn_*`), health figures are read, but px4-gps-lag 0.1.0 cannot yet measure the GPS lag;
the report says so instead of a verdict. Don't invent one.

Never tell the user a vehicle is safe or unsafe to fly on the strength of this report.

## In CI (GitHub Action)

```yaml
- uses: actions/checkout@v4
- uses: munimtechnologies/px4-log-health@v0.3.6
  with:
    logs: "**/*.ulg"          # space-separated globs
    fail-on-flag: "false"     # "true" fails the step on any flag or suggestion
```

The report lands in the job summary, and one JSON line per log in `px4-log-health.jsonl`.

## Sources and limits

- Fleet figures: 208 public Flight Review logs, one per vehicle, uploaded from October 2025
  (`studies/fleet-health-2026/GRADE.md`, pre-registered).
- GPS lag method and fleet figures: px4-gps-lag (`studies/fleet-2026`,
  `studies/mismatch-2026`, `studies/dynmodel-2026`), each pre-registered before data was read.
- Vibration bands from Flight Review; jamming limits from the PX4 docs.
- Nobody at PX4 or Dronecode has reviewed these results. Report problems or questions at
  https://github.com/munimtechnologies/px4-log-health/issues .

MV2, Munim, Inc., mv2@munimtech.com

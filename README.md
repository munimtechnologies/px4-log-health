# px4-log-health

**Try it in your browser:** [munimv2.github.io/px4-log-check](https://munimv2.github.io/px4-log-check/) runs this repo's `checkup.py` on your `.ulg` files on your own computer. Nothing from the log is uploaded.

How healthy was a PX4 flight, by Flight Review's own limits, and how does it compare with other public flights? `health.py` reads one PX4 flight log (ULog) and reports, for the time the vehicle was in the air:

- **vibration**: `vehicle_imu_status.accel_vibration_metric` against the bands Flight Review draws on its vibration plot (green below 4.905 m/s², orange to 9.81, red above). On logs from PX4 v1.12 or older the metric is in m/s, so no band is given (see below);
- **accelerometer clipping**: how much PX4's running clipping totals grew while flying;
- **GNSS**: the jamming indicator against the PX4 docs' limits ("should be around or below 40", "80 or higher" too high), the receiver's own jamming and spoofing states where it reports them, satellites used and eph. Read from `vehicle_gps_position`, or from `vehicle_gnss` on PX4 main (new in 0.3.2);
- **battery**: the highest battery warning (low, critical, emergency, failed) of a monitored battery;
- **failsafe**: whether the vehicle entered failsafe;
- **motor saturation** (multicopters): the share of the flight in which any motor was commanded at 0.99 or more.

```
pip install numpy pyulog
python health.py flight.ulg
```

It prints one JSON line per log with each figure, the PX4 release, a list of plain-language `flags` and a list of `notes`. It reads no position, so no location leaves the log. A flag is a reason to look at the log in Flight Review, not a diagnosis.

**Fleet place.** With `baseline.json` next to `health.py` (it is in this repo), each figure is also placed against 208 public flights, e.g. `"vib_median": "p75-p90"` or `"above p99"`. When most flights share the value, the whole tied range is named, e.g. `"clip_per_min": "p10-p75 (tied)"` for a flight with no clipping. Vibration is placed only against logs from PX4 v1.13 or newer.

## One report per flight

`checkup.py` turns each log into a short Markdown report: the flags, a table of every figure with its place among 208 public flights, and a **GPS delay** verdict. The verdict measures how far the GPS lags the IMU with [px4-gps-lag](https://github.com/munimtechnologies/px4-gps-lag), compares that with the delay EKF2 assumed, and checks the direction against EKF2's own GPS velocity innovations. It suggests a new delay only when the measured lag is more than 50 ms off and the innovations don't point the other way. That is the rule px4-gps-lag's fleet studies support ([GRADE-innov-confirm.md](https://github.com/munimtechnologies/px4-gps-lag/blob/main/studies/fleet-2026/GRADE-innov-confirm.md)). A suggested value is a starting point to check with EKF2 log replay, not a setting to fly.

The delay EKF2 assumed is `EKF2_GPS_DELAY` up to PX4 v1.17. From v1.18 on it is a delay per receiver, `SENS_GPS0_DELAY` / `SENS_GPS1_DELAY` (see 0.3.2 below), and the report names the one to change. When a serial u-blox receiver runs `GPS_UBX_DYNMODEL` 6, the report adds one line about it (see 0.3.4 to 0.3.6 below).

```
pip install numpy pyulog requests
python fetch_deps.py                 # gps_lag.py and innov.py from px4-gps-lag, pinned commit
python checkup.py flight.ulg [more.ulg ...] --md report.md --json rows.jsonl [--fail-on-flag]
```

Without `--md` the report goes to stdout. `--md` appends, so it can point at `$GITHUB_STEP_SUMMARY`. With `--fail-on-flag`, the exit code is 1 when any log raises a flag or gets a suggested GPS delay.

Real reports: three public logs from PX4 v1.13-v1.16 in [studies/fleet-health-2026/sample-report.md](studies/fleet-health-2026/sample-report.md), three public logs from PX4 v1.18 pre-releases in [studies/v118/report.md](studies/v118/report.md), and the two dynamic-model logs from PX4 PR #24831 in [studies/dynmodel/report.md](studies/dynmodel/report.md) (0.3.4), [studies/dynmodel/report-0.3.5.md](studies/dynmodel/report-0.3.5.md) (0.3.5) and [studies/dynmodel/report-0.3.6.md](studies/dynmodel/report-0.3.6.md) (0.3.6).

### In your browser

[munimv2.github.io/px4-log-check](https://munimv2.github.io/px4-log-check/) runs `checkup.py` in [Pyodide](https://pyodide.org/) (Python in WebAssembly), with numpy and pyulog. It runs 0.3.4, from this repo's v0.3.4 tag, so it reads the per-receiver delay of PX4 v1.18 logs and adds the dynamic model line, like the command line. Pick one or more `.ulg` files and the report appears on the page. You can download it as Markdown and download the figures as JSON lines. The page fetches Pyodide and numpy from the jsDelivr CDN, pyulog from PyPI, and the checker's code from this repo's tag and px4-gps-lag at a pinned commit. The log itself stays in the browser tab.

**How it was checked.** A sealed run loaded the page's own `core.js` (0.2.0) in Node with the same Pyodide build (314.0.7). It ran the two logs attached to PX4 PR #24831 through it, downloaded once and checked against the sha256 px4-gps-lag's paired job recorded (`aa6997a9…3b49`). The browser report was identical to [studies/dynmodel/report.md](studies/dynmodel/report.md), the report `checkup.py` 0.3.4 wrote from the command line. Both logs were read as a serial u-blox F9P, and only the model-6 log got the dynamic model line. The page's first version (0.3.1) was checked the same way on the three public logs of the sample report.

### As a GitHub Action

Keep flight logs in a repository (for a test vehicle, say) and get the report in each run's job summary:

```yaml
name: PX4 log health
on: [push, pull_request]
jobs:
  checkup:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: munimtechnologies/px4-log-health@v0.3.6
        with:
          logs: "**/*.ulg"          # space-separated globs
          fail-on-flag: "false"     # "true" fails the step on any flag or suggestion
```

The Action installs numpy and pyulog, fetches `gps_lag.py` and `innov.py` from px4-gps-lag at a pinned commit (input `gps-lag-ref`), and writes one JSON line per log to `px4-log-health.jsonl` (input `json`).

**How it was checked.** A sealed run went through the unit tests (checkup, health and px4-gps-lag's tests). It then ran `checkup.py` and the Action's script, from a bare folder, on three real public logs: one more than 50 ms off its setting, one within 20 ms, and one from PX4 v1.12 or older. Every health figure, lag and implied innovation excess matched the figures the studies had recorded. That run was for 0.3.1; 0.3.2 to 0.3.6 were checked as described below.

### For coding agents (agent skill)

[skills/px4-log-health/SKILL.md](skills/px4-log-health/SKILL.md) tells a coding agent (Claude Code, Codex, Cursor and others that read the open SKILL.md format) when to reach for this checker, how to run it on a user's `.ulg` from the pinned release, and how to explain the report without overstating it: a flag is not a diagnosis, a suggested GPS delay is a value to verify with EKF2 log replay, and PX4 main logs get no GPS delay verdict yet. Install it into a project with the open [skills](https://github.com/vercel-labs/skills) CLI:

```
npx skills add munimtechnologies/px4-log-health --skill px4-log-health
```

The skill only runs the checker locally; it sends no log anywhere.

## What changed in 0.3.6: the fleet shows the same smoothing

- **One more sentence in the dynamic model line.** px4-gps-lag's pre-registered fleet mechanism check ([GRADE-tau-fleet.md](https://github.com/munimtechnologies/px4-gps-lag/blob/main/studies/dynmodel-2026/GRADE-tau-fleet.md)) ran the low-pass-plus-delay fit of 0.3.5's single-vehicle check on dynmodel-2026's own public logs. All 166 logs at models 6 and 7 were downloaded again, and each reproduced its published lag within 2 ms. Within receiver classes (104 logs at model 6 and 59 at model 7), the fitted time constant was a median **110 ms** longer at model 6 (interval 50 to 145), while the delay after the filter was 45 ms shorter (−75 to 0). Their sum, which doesn't depend on how the fit splits the two, was +53.8 ms (40 to 80). So the smoothing seen on the one PR #24831 vehicle shows across public logs too. The line now says so after the single-vehicle figure, and it still suggests trying `GPS_UBX_DYNMODEL` = 7 and measuring again. It is still a note, not a flag. Nothing else changes, and every figure is measured as in 0.3.5.
- **Limits.** In a two-parameter fit the time constant and the delay trade off: 45 of 106 model-6 fits had a negative delay, and 20 had their time constant at the top of the grid (250 ms). The parts that don't rest on the split agree: adding the low-pass removed a median 9.1% of the pure-delay fit's residual variance on model-6 logs, against 1.1% on model-7 logs.

**How it was checked.** A sealed run took 0.3.5 from this repo's main branch and built 0.3.6 from it by five exact text edits: the new sentence, its source comment, the source list, the module docstring and the version. It passed all 100 unit tests of checkup, `test_checkup_dynmodel` (which now also pins the fleet sentence and the version), health and px4-gps-lag. It then ran the whole checkup on the two logs attached to PR #24831: [studies/dynmodel/report-0.3.6.md](studies/dynmodel/report-0.3.6.md) and [smoke-0.3.6.json](studies/dynmodel/smoke-0.3.6.json). The model-6 log got the line once, with the new sentence and GRADE-tau-fleet.md as a source, and the model-7 log did not get it. The lags were 214.2 and 110.5 ms, as before, and no check failed.

## What changed in 0.3.5: why model 6 is better changed than compensated

- **The dynamic model line now says how the extra lag behaves.** px4-gps-lag's pre-registered mechanism check ([GRADE-mechanism.md](https://github.com/munimtechnologies/px4-gps-lag/blob/main/studies/dynmodel-2026/GRADE-mechanism.md)) fitted the two logs attached to PX4 PR #24831 as a first-order low-pass followed by a delay. At model 6 the fitted time constant was **110 ms** (90 to 140), and at model 7 it was **20 ms** (0 to 60). The delay after the filter barely moved (110 against 95 ms). So model 6's extra lag behaved like heavier smoothing, which no single GPS delay setting fully cancels: a filter delays slow manoeuvres more than fast ones. The line now says so, and still suggests trying `GPS_UBX_DYNMODEL` = 7 and measuring again. It is still a note, not a flag. Nothing else changes, and every figure is measured as in 0.3.4.
- **How many vehicles it concerns.** px4-gps-lag's pre-registered census ([census-2026/GRADE.md](https://github.com/munimtechnologies/px4-gps-lag/blob/main/studies/census-2026/GRADE.md)) read one public log each from 250 v1.16 and 250 v1.17 multicopters. **81.6%** of the v1.16 multicopters ran model 6. On v1.17, **5.6%** still did, almost all with 6 set against the new default. If you upgraded with an old parameter file, this line is the one to look for.

**How it was checked.** A sealed run took 0.3.4 from this repo's main branch and compared it with 0.3.5. The only changes are the line's wording, its source comment, the module docstring and the version. It passed all 100 unit tests of checkup, `test_checkup_dynmodel` (which now also pins the smoothing figures), health and px4-gps-lag. It then ran the whole checkup on the two logs attached to PR #24831: [studies/dynmodel/report-0.3.5.md](studies/dynmodel/report-0.3.5.md) and [smoke-0.3.5.json](studies/dynmodel/smoke-0.3.5.json). The model-6 log got the line once and the model-7 log did not. The lags were 214.2 and 110.5 ms, as before, and no check failed.

## What changed in 0.3.4: the u-blox dynamic model

- **One information line for u-blox receivers at `GPS_UBX_DYNMODEL` 6.** u-blox receivers filter their output according to a "dynamic platform model". Model 6 is "airborne <1g". PX4's `rc.mc_defaults` set it for every multicopter up to v1.16. PX4 PR [#24831](https://github.com/PX4/PX4-Autopilot/pull/24831) moved multicopters to 7 (<2g) in v1.17, because 6 "can upset the EKF" in aggressive flight. px4-gps-lag measured what it does to GPS lag in two pre-registered checks:
  - **Across public logs** ([dynmodel-2026](https://github.com/munimtechnologies/px4-gps-lag/blob/main/studies/dynmodel-2026/GRADE.md), 244 u-blox vehicles): model-6 logs lagged a median **46 ms** more than model-7 logs from the same receiver class (interval 33 to 55). 42% of them were more than 50 ms past their delay setting, against 9% at model 7. Model 8 (<4g) was no different from 7.
  - **Within one vehicle** ([GRADE-paired.md](https://github.com/munimtechnologies/px4-gps-lag/blob/main/studies/dynmodel-2026/GRADE-paired.md)): on the two logs attached to PR #24831 (same vehicle, u-blox F9P, flown the same day), the GPS lagged **214 ms at model 6 and 110 ms at model 7**.

  When the log's GNSS receiver is a u-blox on a serial port (the receivers PX4's own driver configures) and `GPS_UBX_DYNMODEL` is 6, the report now ends its GPS section with those figures. It suggests trying 7 (a reboot applies it) and measuring again. It is a note, not a flag, so `--fail-on-flag` ignores it. The JSON row has a new `gps_dynmodel` field (`value`, `device_id`, `devtype`, `note`) for every serial u-blox log that has the parameter. Nothing else changes, and every figure is measured as in 0.3.3.

**How it was checked.** A sealed run took 0.3.3 from this repo's main branch and compared it with 0.3.4. The only changes are the new line and its source comment, `dynmodel_info()`, the `gps_dynmodel` JSON field (the log's parameters are now read even when the GPS check is skipped, so the field is there either way), the version and the tests. It passed all 98 unit tests of checkup (including the new `test_checkup_dynmodel`), health and px4-gps-lag. It then ran the whole checkup on the two logs attached to PR #24831, downloaded once and checked against the sha256 that px4-gps-lag's paired job recorded: [studies/dynmodel/report.md](studies/dynmodel/report.md) and [smoke.json](studies/dynmodel/smoke.json). Both logs were read as a serial u-blox F9P (device type 0xAA). The model-6 log got the line and the model-7 log did not. The lags were 214.2 and 110.5 ms, the figures px4-gps-lag published, and no check failed.

## What changed in 0.3.3: what a mismatched delay does to EKF2, and a real v1.18 run

- **One more line on logs more than 50 ms off.** px4-gps-lag's pre-registered [mismatch-2026](https://github.com/munimtechnologies/px4-gps-lag/blob/main/studies/mismatch-2026/GRADE.md) study read 256 public logs. On the 93 logs more than 50 ms off their setting, EKF2's GPS velocity test ratio while accelerating was a median 2.7 times its steady-flight value, against 1.7 times on the 163 others. 20% of mismatched logs had a GPS velocity rejection in flight, against 16%. That gap is within chance. The plan's rule D1 says the checkup should report this, so every "more than 50 ms off" verdict now ends with that line, with or without a suggestion. Nothing else in the report changes and every figure is measured as in 0.3.2.
- **The v1.18 path, on real logs.** A sealed run downloaded three public logs from PX4 v1.18 pre-releases (one u-blox M8, one NMEA and one u-blox M9 receiver; none has `EKF2_GPS_DELAY`) and ran the whole checkup on them: [studies/v118/report.md](studies/v118/report.md). In all three the checkup read the delay EKF2 applied as 110 ms from `SENS_GPS0_DELAY` (both `SENS_GPSn_ID` unset, so PX4 picks the slot by order). The M8 log lags 100 ms (−10 ms) and the M9 log 71 ms (−39 ms): nothing to change. The NMEA log lags 187 ms (+77 ms), EKF2's own innovations lean the same way (implied +57 ms), so the report suggests **SENS_GPS0_DELAY = 187** as a starting value and adds the mismatch-2026 line. Those lags are the ones px4-gps-lag's fresh-2026 sample recorded for the same logs (100.4, 186.9 and 70.7 ms).

**How it was checked.** A sealed run took 0.3.2 from this repo's main branch, compared it with 0.3.3 (the only changes are the new line, its source comment, the version and the tests), passed all 84 unit tests of checkup, health and px4-gps-lag, and then ran the checkup on the three v1.18 logs with no error.

## What changed in 0.3.2: PX4 v1.18 and PX4 main logs

PX4 changed how it stores the GPS delay and what it calls the GPS topics. On logs from the new firmware 0.3.1 still ran, but it said the log had no delay setting and never suggested one.

- **PX4 v1.18: a delay per receiver.** [PX4 PR #26660](https://github.com/PX4/PX4-Autopilot/pull/26660) (on main since March 2026, in v1.18.0-rc1) removed `EKF2_GPS_DELAY`. The sensors module now sets each sample's `timestamp_sample` to its arrival time minus `SENS_GPS0_DELAY` or `SENS_GPS1_DELAY`. It matches the slot to the receiver through `SENS_GPS0_ID` / `SENS_GPS1_ID`, by order when no id is set, and uses 110 ms when ids are set but none matches. A PPS correction takes priority. EKF2 then fuses the sample at `timestamp_sample`. checkup 0.3.2 reads the delay EKF2 actually applied from the log itself (arrival time minus `timestamp_sample`) and names the parameter behind it:
  - "this vehicle's SENS_GPS0_DELAY is 110 ms ..." and **try SENS_GPS0_DELAY = N** when the lag is more than 50 ms off;
  - when the receiver matches no `SENS_GPSn_ID`, it says PX4 used its 110 ms default and suggests setting `SENS_GPS0_ID` to the receiver's device id along with the delay;
  - when PPS or the GPS driver set the sample times, it says so and suggests no parameter.
  Logs with `EKF2_GPS_DELAY` (v1.17 and older) are handled exactly as before.
- **PX4 main: GPS becomes GNSS.** [PX4 PR #24399](https://github.com/PX4/PX4-Autopilot/pull/24399) (main since 29 September 2026) renamed `vehicle_gps_position` to `vehicle_gnss`, with the receiver's sample nested as `receiver`, and `SENS_GPSn_*` to `SENS_GNSSn_*`. health 0.2.2 reads the jamming, spoofing, satellite and eph figures from `vehicle_gnss`, and checkup reads `SENS_GNSS0_DELAY`. px4-gps-lag's `gps_lag.py` doesn't read `vehicle_gnss` yet. On those logs the report says so instead of showing a bare error.

**How it was checked.** A sealed run passed all 81 unit tests, old and new: synthetic v1.18 and PX4 main sample times and parameters, id matching (including ids above 2^31, which PX4 stores as negative int32), PPS timing, and a `vehicle_gnss` copy of the synthetic flight giving the same health figures as the `vehicle_gps_position` original. The same run read two real ULog files from pyulog's test folder through the new code and the whole checkup without an error. Separately, px4-gps-lag's fresh-2026 sample found 8 public logs from PX4 v1.18 pre-releases: none has `EKF2_GPS_DELAY`, and in every one the arrival time minus `timestamp_sample` is 110.0 ms, the value checkup 0.3.2 reads ([GRADE-fresh.md](https://github.com/munimtechnologies/px4-gps-lag/blob/main/studies/receiver-2026/GRADE-fresh.md)). The whole checkup on three of those logs is described under 0.3.3 above. No public log in that sample used `vehicle_gnss`, so the `receiver.` field names it reads there come from pyulog's naming of nested fields and are not yet seen on a real log.

## What changed in 0.3.1

Two wording fixes; every figure is measured exactly as in 0.3.0.

- **Tied fleet places.** When several fleet quantiles equal the flight's value (zero clipping or zero motor saturation, which most public flights share), the fleet column now names the whole tied range, e.g. "p10-p75 (tied)", with a one-line explanation under the table. 0.3.0 showed the highest band the value reached ("p75-p90"), which read as if the flight clipped more than most.
- **Close to the 50 ms line.** When the GPS lag is within 50 ms of `EKF2_GPS_DELAY` but 40 ms or more off, the report shows the lag and the difference to one decimal and says it is close to the line: no change is suggested, and another flight will show which side it falls on. 0.3.0 printed "+50 ms" and "within 50 ms" for a difference of +49.7 ms. The JSON verdict has a new `near_line` field.

## What changed in 0.2.0

Two things the fleet study found are now handled:

- **Vibration units by release.** PX4 v1.12 and older compute the vibration metric from delta velocities (m/s per IMU interval), about 200 times smaller than the m/s² metric of v1.13 and newer, so Flight Review's bands don't apply. 0.2.0 reads the release from the log and gives no band for those logs, with a note. If the release can't be read, it assumes m/s² and says so.
- **Jamming state 2 is a note, not a flag.** A receiver jamming state of 2 (u-blox: interference visible, fix kept) shows up in 70% of public logs that report a state. State 3 (critical) is flagged; it was rare (2 of 77 logs).

0.2.0 measures every figure exactly as 0.1.0 did. A sealed check re-measured three real logs (one v1.11, two v1.16) and every figure matched (`studies/fleet-health-2026/smoke.jsonl`).

## What 208 public PX4 flights look like

`studies/fleet-health-2026/` was pre-registered (`PLAN.md`) before any log was downloaded. It measured 208 real flights from 208 vehicles, uploaded to Flight Review from October 2025 on, one per vehicle ([GRADE.md](studies/fleet-health-2026/GRADE.md)):

- **75%** raised at least one flag.
- **Vibration:** among logs from PX4 v1.13 or newer, **51%** sit in Flight Review's orange or red band, and 27% in red. Across all logs, the pre-registered figure is 40%; it is lower because the older logs read low.
- **Clipping:** 23% clipped the accelerometer in flight, 30% of loud logs against 18% of quiet ones.
- **GPS jamming indicator:** 30% have a median above the docs' 40.
- **Spoofing:** 8% of logs that report a spoofing state reached "indicated".
- **Battery and failsafe:** 19% reached a low-battery warning, and 26% entered failsafe in flight.
- **Motor saturation:** 4.5% of multicopters had a motor at full command for 5% of the flight or more.
- **GPS lag replication:** on 98 of these logs, the median GPS lag was 113.8 ms, and 31% were more than 50 ms off their `EKF2_GPS_DELAY`. This replicates [px4-gps-lag](https://github.com/munimtechnologies/px4-gps-lag)'s fleet-2026 figures (99.8 ms; 29.6%) on vehicles that study never saw. On the same 98 logs, EKF2's own GPS velocity innovations agreed with the measured mismatch (px4-gps-lag, `studies/fleet-2026/GRADE-innov-confirm.md`).

Six of ten health predictions held. Our priors on vibration and jamming were too low. These are uploaded logs, often uploaded because something went wrong, so they are not the rate in the air. `logs.jsonl` (one line per log; vehicle ids hashed, no positions), `summary.json` and `baseline.json` (fleet quantiles, all releases) are in the study folder. The `baseline.json` at the repo root is the one `health.py` uses: the same fleet, with vibration quantiles from v1.13+ logs only.

## Tests

```
pip install numpy pyulog ijson requests
python fetch_deps.py      # gps_lag.py, innov.py, fleet.py and test_gps_lag.py from px4-gps-lag
python -m unittest -v test_checkup test_checkup_dynmodel test_health test_gps_lag
```

`smoke.py` re-measures three real logs from the study and checks them against the recorded figures. `smoke_checkup.py` does the same through `checkup.py` and the Action's script. Both download from Flight Review.

## Licence and credits

Code: Apache-2.0. Text: CC BY 4.0. Results derived from PX4 Flight Review public logs (Dronecode / PX4, CC BY 4.0). Reads logs with [pyulog](https://github.com/PX4/pyulog) (BSD-3-Clause). Limits quoted from the PX4 docs and Flight Review. The browser page uses [Pyodide](https://pyodide.org/) (MPL-2.0). Parameter and topic names quoted from PX4-Autopilot (BSD-3-Clause). The dynamic-model report reads the two logs a PX4 maintainer attached to PX4-Autopilot PR #24831; they are not republished here.

By MV2 of Munim, Inc. (mv2@munimtech.com). The code and text were made with AI agents working in his name.

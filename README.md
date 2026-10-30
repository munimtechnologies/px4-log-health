# px4-log-health

How healthy was a PX4 flight, by Flight Review's own limits, and how does it compare with other public flights? `health.py` reads one PX4 flight log (ULog) and reports, for the time the vehicle was in the air:

- **vibration**: `vehicle_imu_status.accel_vibration_metric` against the bands Flight Review draws on its vibration plot (green below 4.905 m/s², orange to 9.81, red above). On logs from PX4 v1.12 or older the metric is in m/s, so no band is given (see below);
- **accelerometer clipping**: how much PX4's running clipping totals grew while flying;
- **GNSS**: the jamming indicator against the PX4 docs' limits ("should be around or below 40", "80 or higher" too high), the receiver's own jamming and spoofing states where it reports them, satellites used and eph;
- **battery**: the highest battery warning (low, critical, emergency, failed) of a monitored battery;
- **failsafe**: whether the vehicle entered failsafe;
- **motor saturation** (multicopters): the share of the flight in which any motor was commanded at 0.99 or more.

```
pip install numpy pyulog
python health.py flight.ulg
```

It prints one JSON line per log with each figure, the PX4 release, a list of plain-language `flags` and a list of `notes`. It reads no position, so no location leaves the log. A flag is a reason to look at the log in Flight Review, not a diagnosis.

**Fleet place (new in 0.2.0).** With `baseline.json` next to `health.py` (it is in this repo), each figure is also placed against 208 public flights, e.g. `"vib_median": "p75-p90"` or `"above p99"`. Vibration is placed only against logs from PX4 v1.13 or newer.

## One report per flight (new in 0.3.0)

`checkup.py` turns each log into a short Markdown report: the flags, a table of every figure with its place among 208 public flights, and a **GPS delay** verdict. The verdict measures how far the GPS lags the IMU with [px4-gps-lag](https://github.com/munimtechnologies/px4-gps-lag), compares that with the log's `EKF2_GPS_DELAY`, and checks the direction against EKF2's own GPS velocity innovations. It suggests a new `EKF2_GPS_DELAY` only when the measured lag is more than 50 ms off the setting and the innovations don't point the other way. That is the rule px4-gps-lag's fleet studies support ([GRADE-innov-confirm.md](https://github.com/munimtechnologies/px4-gps-lag/blob/main/studies/fleet-2026/GRADE-innov-confirm.md)). A suggested value is a starting point to check with EKF2 log replay, not a setting to fly.

```
pip install numpy pyulog requests
python fetch_deps.py                 # gps_lag.py and innov.py from px4-gps-lag, pinned commit
python checkup.py flight.ulg [more.ulg ...] --md report.md --json rows.jsonl [--fail-on-flag]
```

Without `--md` the report goes to stdout. `--md` appends, so it can point at `$GITHUB_STEP_SUMMARY`. With `--fail-on-flag`, the exit code is 1 when any log raises a flag or gets a suggested `EKF2_GPS_DELAY`.

Here is a real report on three public logs: [studies/fleet-health-2026/sample-report.md](studies/fleet-health-2026/sample-report.md).

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
      - uses: munimtechnologies/px4-log-health@v0.3.0
        with:
          logs: "**/*.ulg"          # space-separated globs
          fail-on-flag: "false"     # "true" fails the step on any flag or suggestion
```

The Action installs numpy and pyulog, fetches `gps_lag.py` and `innov.py` from px4-gps-lag at a pinned commit (input `gps-lag-ref`), and writes one JSON line per log to `px4-log-health.jsonl` (input `json`).

**How it was checked.** A sealed run went through the unit tests (checkup, health and px4-gps-lag's tests). It then ran `checkup.py` and the Action's script, from a bare folder, on three real public logs: one more than 50 ms off its setting, one within 20 ms, and one from PX4 v1.12 or older. Every health figure, lag and implied innovation excess matched the figures the studies had recorded.

**Known in 0.3.0.** When most public flights share one value (no clipping, for example), the fleet column shows the highest band that value reaches, e.g. "p75-p90" for zero clipping events. Read it as "like most flights". 0.3.1 will name the whole tied range instead.

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
python -m unittest -v test_checkup test_health test_gps_lag
```

`smoke.py` re-measures three real logs from the study and checks them against the recorded figures. `smoke_checkup.py` does the same through `checkup.py` and the Action's script. Both download from Flight Review.

## Licence and credits

Code: Apache-2.0. Text: CC BY 4.0. Results derived from PX4 Flight Review public logs (Dronecode / PX4, CC BY 4.0). Reads logs with [pyulog](https://github.com/PX4/pyulog) (BSD-3-Clause). Limits quoted from the PX4 docs and Flight Review.

By MV2 of Munim, Inc. (mv2@munimtech.com). The code and text were made with AI agents working in his name.

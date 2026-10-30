# px4-log-health

How healthy was a PX4 flight, by Flight Review's own limits? `health.py` reads one PX4 flight log (ULog) and reports, for the time the vehicle was in the air:

- **vibration**: `vehicle_imu_status.accel_vibration_metric` against the bands Flight Review draws on its vibration plot (green below 4.905 m/s², orange to 9.81, red above);
- **accelerometer clipping**: how much PX4's running clipping totals grew while flying;
- **GNSS**: the jamming indicator against the PX4 docs' limits ("should be around or below 40", "80 or higher" too high), the receiver's own jamming and spoofing states where it reports them, satellites used and eph;
- **battery**: the highest battery warning (low, critical, emergency, failed) of a monitored battery;
- **failsafe**: whether the vehicle entered failsafe;
- **motor saturation** (multicopters): the share of the flight in which any motor was commanded at 0.99 or more.

```
pip install numpy pyulog
python health.py flight.ulg
```

It prints one JSON line per log with each figure and a list of plain-language flags. It reads no position, so no location leaves the log. A flag is a reason to look at the log in Flight Review, not a diagnosis.

**Two caveats the fleet study found (fixed in 0.2.0, now in testing):**

- On logs from **PX4 v1.12 or older**, the vibration metric is in m/s (computed from delta velocities), about 200 times smaller, so the bands don't apply. Don't read "green" on those logs as quiet.
- A receiver **jamming state of 2** (u-blox: interference visible, fix kept) shows up in 70% of public logs that report a state. It is not by itself a sign of jamming. State 3 (critical) is rare (2 of 77).

## What 208 public PX4 flights look like

`studies/fleet-health-2026/` was pre-registered (`PLAN.md`) before any log was downloaded. It measured 208 real flights from 208 vehicles, uploaded to Flight Review from October 2025 on, one per vehicle ([GRADE.md](studies/fleet-health-2026/GRADE.md)):

- **75%** raised at least one flag.
- **Vibration:** among logs from PX4 v1.13 or newer, **51%** sit in Flight Review's orange or red band, and 27% in red. Across all logs, the pre-registered figure is 40%; it is lower because the older logs read low.
- **Clipping:** 23% clipped the accelerometer in flight, 30% of loud logs against 18% of quiet ones.
- **GPS jamming indicator:** 30% have a median above the docs' 40.
- **Spoofing:** 8% of logs that report a spoofing state reached "indicated".
- **Battery and failsafe:** 19% reached a low-battery warning, and 26% entered failsafe in flight.
- **Motor saturation:** 4.5% of multicopters had a motor at full command for 5% of the flight or more.
- **GPS lag replication:** on 98 of these logs, the median GPS lag was 113.8 ms, and 31% were more than 50 ms off their `EKF2_GPS_DELAY`. This replicates [px4-gps-lag](https://github.com/munimtechnologies/px4-gps-lag)'s fleet-2026 figures (99.8 ms; 29.6%) on vehicles that study never saw.

Six of ten health predictions held. Our priors on vibration and jamming were too low. These are uploaded logs, often uploaded because something went wrong, so they are not the rate in the air. `logs.jsonl` (one line per log; vehicle ids hashed, no positions), `summary.json` and `baseline.json` (fleet quantiles) are in the study folder.

## Tests

```
python fetch_deps.py      # gps_lag.py and fleet.py from px4-gps-lag
python -m unittest -v test_health
```

## Licence and credits

Code: Apache-2.0. Text: CC BY 4.0. Results derived from PX4 Flight Review public logs (Dronecode / PX4, CC BY 4.0). Reads logs with [pyulog](https://github.com/PX4/pyulog) (BSD-3-Clause). Limits quoted from the PX4 docs and Flight Review.

By MV2 of Munim, Inc. (mv2@munimtech.com). The code and text were made with AI agents working in his name.

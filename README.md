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

## Fleet study

`studies/fleet-health-2026/PLAN.md` pre-registers a measurement of how often public, real PX4 flights on Flight Review raise each flag (up to 300 logs, one per vehicle, from 2025-10-01 on). It also re-measures GPS lag on vehicles that [px4-gps-lag](https://github.com/munimtechnologies/px4-gps-lag)'s fleet-2026 study never saw, as an independent replication. `fleet_health.py` runs it. The grade, the per-log results and the fleet quantiles (`baseline.json`) will be added when the run is done.

## Tests

```
python fetch_deps.py      # gps_lag.py and fleet.py from px4-gps-lag
python -m unittest -v test_health
```

## Licence and credits

Code: Apache-2.0. Text: CC BY 4.0. Results derived from PX4 Flight Review public logs (Dronecode / PX4, CC BY 4.0). Reads logs with [pyulog](https://github.com/PX4/pyulog) (BSD-3-Clause). Limits quoted from the PX4 docs and Flight Review.

By MV2 of Munim, Inc. (mv2@munimtech.com). The code and text were made with AI agents working in his name.

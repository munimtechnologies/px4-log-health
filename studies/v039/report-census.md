# PX4 log health

3 logs, checked with px4-log-health checkup 0.3.9 by MV2 of Munim, Inc. (made with AI agents). A flag is a reason to look at the log in Flight Review, not a diagnosis.

## 026bcfd6-de20-4069-b3cd-e15a4a21a66f.ulg

PX4 v1.16.2, 2.5 min in the air, multicopter.

**2 flags**

- vibration orange (median 6.0 m/s^2; Flight Review's plot turns orange at 4.9 and red at 9.8)
- failsafe in flight

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 6.0 m/s² (orange) | p50-p75 |
| Vibration, 95th percentile | 7.6 m/s² | p25-p50 |
| Accelerometer clipping | 0 events (0.00 per min) | p10-p75 (tied) |
| GPS receiver jamming state, max | 0 (unknown) |  |
| Satellites used, median | 42 | p75-p90 |
| eph, median | 0.01 m | p10-p25 |
| Battery warning, worst | none |  |
| Failsafe in flight | yes |  |
| A motor at full command | 0.0% of flight | p10-p50 (tied) |

*(tied)*: many public flights have exactly this value (for clipping and motor saturation, usually zero), so this flight sits with them.

### GPS delay

Not measured: IMU explains too little of the GPS velocity change. The lag needs a flight with some manoeuvring; hover-only flights usually can't be measured.

### Compass

Compass barely moves with battery current: the field norm moves 1.5% over this flight's current range (24.6-44.3 A, r = 0.21); fleet place p25-p50 of 63 public PX4 flights.

### Battery

PX4's battery-resistance estimate was 0 for the whole second half of the flight (it was already 0 when the vehicle took off), but BAT1_R_INTERNAL is set (5.0 mOhm per cell), so PX4 used that value, not the estimate.

## 03ce3cc6-db48-4134-b571-d98d4b03d3e4.ulg

PX4 v1.16.1-192, 5.7 min in the air, multicopter.

**3 flags**

- vibration orange (median 5.7 m/s^2; Flight Review's plot turns orange at 4.9 and red at 9.8)
- GPS jamming indicator median 109 (PX4 docs: should be around or below 40)
- battery warning level 1 in flight

Notes:

- GPS receiver reported interference (jamming state 2: visible, fix kept); common on ordinary flights (70% of public logs that report a state)

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 5.7 m/s² (orange) | p50-p75 |
| Vibration, 95th percentile | 7.2 m/s² | p25-p50 |
| Accelerometer clipping | 0 events (0.00 per min) | p10-p75 (tied) |
| GPS jamming indicator, median | 109 (docs: about 40 or below) | p90-p99 |
| GPS receiver jamming state, max | 2 (warning) |  |
| Satellites used, median | 12 | below p10 |
| eph, median | 3.10 m | p90-p99 |
| Battery warning, worst | low |  |
| Failsafe in flight | no |  |
| A motor at full command | 0.0% of flight | p10-p50 (tied) |

*(tied)*: many public flights have exactly this value (for clipping and motor saturation, usually zero), so this flight sits with them.

### GPS delay

The GPS lags the IMU by **151.6 ms** (± 1.5); this vehicle's EKF2_GPS_DELAY is 110 ms (+41.6 ms).

EKF2's own GPS velocity innovations lean the same way (implied +74 ms).

Within 50 ms of the setting, but close to the 50 ms line (+41.6 ms): no change suggested; measure another flight to see which side it falls on.

### Compass

Compass barely moves with battery current: the field norm moves 5.7% over this flight's current range (2.1-25.0 A, r = 0.17); fleet place p50-p75 of 63 public PX4 flights.

### Battery

**Note:** PX4's battery-resistance estimate was 0 for the whole second half of the flight (it first reached 0 6.4 s after takeoff) while BAT1_R_INTERNAL is -1 (use the estimate), so PX4 did not correct the voltage-based state of charge for the voltage drop under load: it reads low under load and recovers after landing. On public PX4 flights this happened on 9 of 53 that use the estimate. A wrong current sensor scale or offset can also cause it. See PX4's battery estimation guide: https://docs.px4.io/main/en/config/battery

## 9f9268ce-1bc2-4a45-aa27-77f55cea35e7.ulg

PX4 v1.16.1, 2.0 min in the air, multicopter.

**2 flags**

- vibration red (median 21.8 m/s^2; Flight Review's plot turns orange at 4.9 and red at 9.8)
- accelerometer clipped 140 times in flight

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 21.8 m/s² (red) | p90-p99 |
| Vibration, 95th percentile | 27.9 m/s² | p90-p99 |
| Accelerometer clipping | 140 events (70.71 per min) | p90-p99 |
| Battery warning, worst | none |  |
| Failsafe in flight | no |  |
| A motor at full command | 0.3% of flight | p75-p90 |

### GPS delay

Not measured: error: no vehicle_gps_position in the log. The lag needs a flight with some manoeuvring; hover-only flights usually can't be measured.

### Compass

Compass not checked: current range under 5 A.

### Battery

PX4's battery-resistance estimate did not collapse: it ended the flight at 10.5 mOhm per cell (public PX4 flights: median 2.7, p10-p90 1.2-7.5).


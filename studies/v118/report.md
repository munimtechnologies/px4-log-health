# PX4 log health

3 logs, checked with px4-log-health checkup 0.3.3 by MV2 of Munim, Inc. (made with AI agents). A flag is a reason to look at the log in Flight Review, not a diagnosis.

## b1afa1e6-1300-487d-afe9-733179f25fc7.ulg

PX4 v1.18.0-64, 16.5 min in the air, multicopter.

**2 flags**

- vibration orange (median 5.3 m/s^2; Flight Review's plot turns orange at 4.9 and red at 9.8)
- battery warning level 3 in flight

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 5.3 m/s² (orange) | p50-p75 |
| Vibration, 95th percentile | 6.8 m/s² | p25-p50 |
| Accelerometer clipping | 0 events (0.00 per min) | p10-p75 (tied) |
| GPS jamming indicator, median | 21 (docs: about 40 or below) | p50-p75 |
| GPS receiver jamming state, max | 0 (unknown) |  |
| Satellites used, median | 19 | p25-p50 |
| eph, median | 0.77 m | p50-p75 |
| Battery warning, worst | emergency |  |
| Failsafe in flight | no |  |
| A motor at full command | 0.1% of flight | p75-p90 |

*(tied)*: many public flights have exactly this value (for clipping and motor saturation, usually zero), so this flight sits with them.

### GPS delay

The GPS lags the IMU by **100 ms** (± 0.9); this vehicle's SENS_GPS0_DELAY is 110 ms, and EKF2 fused the GPS samples that much before they arrived (-10 ms).

(PX4 v1.18 replaced EKF2_GPS_DELAY with a delay per receiver, SENS_GPS0_DELAY and SENS_GPS1_DELAY; PX4 main names them SENS_GNSS0_DELAY and SENS_GNSS1_DELAY. The delay above is read from the log's own sample times.)

EKF2's own GPS velocity innovations lean the same way (implied -10 ms).

Within 50 ms of the setting: nothing to change.

## b153bdd3-4a48-4ba2-85d0-b0fffc164dc7.ulg

PX4 v1.18.0-128, 4.2 min in the air, multicopter.

**3 flags**

- vibration orange (median 9.5 m/s^2; Flight Review's plot turns orange at 4.9 and red at 9.8)
- battery warning level 3 in flight
- failsafe in flight

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 9.5 m/s² (orange) | p50-p75 |
| Vibration, 95th percentile | 15.6 m/s² | p50-p75 |
| Accelerometer clipping | 0 events (0.00 per min) | p10-p75 (tied) |
| GPS receiver jamming state, max | 0 (unknown) |  |
| Satellites used, median | 12 | below p10 |
| eph, median | 0.00 m | below p10 |
| Battery warning, worst | emergency |  |
| Failsafe in flight | yes |  |
| A motor at full command | 0.0% of flight | p10-p50 (tied) |

*(tied)*: many public flights have exactly this value (for clipping and motor saturation, usually zero), so this flight sits with them.

### GPS delay

The GPS lags the IMU by **187 ms** (± 0.8); this vehicle's SENS_GPS0_DELAY is 110 ms, and EKF2 fused the GPS samples that much before they arrived (+77 ms).

(PX4 v1.18 replaced EKF2_GPS_DELAY with a delay per receiver, SENS_GPS0_DELAY and SENS_GPS1_DELAY; PX4 main names them SENS_GNSS0_DELAY and SENS_GNSS1_DELAY. The delay above is read from the log's own sample times.)

EKF2's own GPS velocity innovations lean the same way (implied +57 ms).

**Suggestion:** try SENS_GPS0_DELAY = 187 as a starting value. Measure two or three flights first (one vehicle's flights agree within a few ms), and check the change with EKF2 log replay before flying it. On public logs, about 30% of vehicles are more than 50 ms off their setting.

On public PX4 logs, a delay this far off went with higher EKF2 GPS velocity test ratios while accelerating (median lift 0.43 vs 0.24: the ratio while accelerating was 2.7 times its steady-flight value, against 1.7 times on logs within 50 ms), and mismatched logs saw more GPS velocity rejections (20% of logs against 16%, a gap within chance on 93 and 163 logs). Source: px4-gps-lag, studies/mismatch-2026/GRADE.md.

## 4f57992c-98d1-49f3-a5d4-4e30d366f0a5.ulg

PX4 v1.18.0-64, 5.1 min in the air, multicopter.

**2 flags**

- vibration orange (median 5.3 m/s^2; Flight Review's plot turns orange at 4.9 and red at 9.8)
- GPS jamming indicator median 55 (PX4 docs: should be around or below 40)

Notes:

- GPS receiver reported interference (jamming state 2: visible, fix kept); common on ordinary flights (70% of public logs that report a state)

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 5.3 m/s² (orange) | p50-p75 |
| Vibration, 95th percentile | 8.1 m/s² | p50-p75 |
| Accelerometer clipping | 0 events (0.00 per min) | p10-p75 (tied) |
| GPS jamming indicator, median | 55 (docs: about 40 or below) | p75-p90 |
| GPS receiver jamming state, max | 2 (warning) |  |
| Satellites used, median | 28 | p50-p75 |
| eph, median | 0.55 m | p50-p75 |
| Battery warning, worst | none |  |
| Failsafe in flight | no |  |
| A motor at full command | 0.0% of flight | p10-p50 (tied) |

*(tied)*: many public flights have exactly this value (for clipping and motor saturation, usually zero), so this flight sits with them.

### GPS delay

The GPS lags the IMU by **71 ms** (± 1.4); this vehicle's SENS_GPS0_DELAY is 110 ms, and EKF2 fused the GPS samples that much before they arrived (-39 ms).

(PX4 v1.18 replaced EKF2_GPS_DELAY with a delay per receiver, SENS_GPS0_DELAY and SENS_GPS1_DELAY; PX4 main names them SENS_GNSS0_DELAY and SENS_GNSS1_DELAY. The delay above is read from the log's own sample times.)

EKF2's own GPS velocity innovations lean the same way (implied -31 ms).

Within 50 ms of the setting: nothing to change.


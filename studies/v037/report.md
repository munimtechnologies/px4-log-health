# PX4 log health

3 logs, checked with px4-log-health checkup 0.3.7 by MV2 of Munim, Inc. (made with AI agents). A flag is a reason to look at the log in Flight Review, not a diagnosis.

## 5f1584c9-83ad-4d5e-97ac-46b3ed19f8e6.ulg

PX4 v1.18.0-128, 3.9 min in the air, multicopter.

**1 flag**

- vibration orange (median 6.2 m/s^2; Flight Review's plot turns orange at 4.9 and red at 9.8)

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 6.2 m/s² (orange) | p50-p75 |
| Vibration, 95th percentile | 11.0 m/s² | p50-p75 |
| Accelerometer clipping | 0 events (0.00 per min) | p10-p75 (tied) |
| GPS receiver jamming state, max | 0 (unknown) |  |
| Satellites used, median | 37 | p75-p90 |
| eph, median | 0.65 m | p50-p75 |
| Failsafe in flight | no |  |
| A motor at full command | 0.0% of flight | p10-p50 (tied) |

*(tied)*: many public flights have exactly this value (for clipping and motor saturation, usually zero), so this flight sits with them.

### GPS delay

The GPS lags the IMU by **166 ms** (± 0.8); EKF2 fused this log's GPS samples 86 ms before they arrived, a shift the DroneCAN receiver's own time stamps set, not SENS_GNSSn_DELAY (+81 ms).

(PX4 v1.18 replaced EKF2_GPS_DELAY with a delay per receiver, SENS_GPS0_DELAY and SENS_GPS1_DELAY; PX4 main names them SENS_GNSS0_DELAY and SENS_GNSS1_DELAY. The delay above is read from the log's own sample times.)

EKF2's own GPS velocity innovations lean the same way (implied +54 ms).

More than 50 ms off, but this DroneCAN receiver's own time stamps set its sample times (PX4 PR #27427, in v1.18 and main), and PX4 then does not apply SENS_GNSSn_DELAY, so changing that parameter would not change when EKF2 fuses its samples, so there is no parameter to suggest; look at this flight in EKF2 log replay.

On public PX4 logs, a delay this far off went with higher EKF2 GPS velocity test ratios while accelerating (median lift 0.43 vs 0.24: the ratio while accelerating was 2.7 times its steady-flight value, against 1.7 times on logs within 50 ms), and mismatched logs saw more GPS velocity rejections (20% of logs against 16%, a gap within chance on 93 and 163 logs). Source: px4-gps-lag, studies/mismatch-2026/GRADE.md.

## 5548f136-8ecc-4b45-8c14-721bdf888a62.ulg

PX4 v1.18.0-128, 2.2 min in the air, multicopter.

**1 flag**

- vibration red (median 12.3 m/s^2; Flight Review's plot turns orange at 4.9 and red at 9.8)

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 12.3 m/s² (red) | p75-p90 |
| Vibration, 95th percentile | 14.2 m/s² | p50-p75 |
| Accelerometer clipping | 0 events (0.00 per min) | p10-p75 (tied) |
| GPS jamming indicator, median | -1 (docs: about 40 or below) | p10-p25 |
| GPS receiver jamming state, max | 0 (unknown) |  |
| Satellites used, median | 32 | p75-p90 |
| eph, median | 0.01 m | below p10 |
| Battery warning, worst | none |  |
| Failsafe in flight | no |  |
| A motor at full command | 0.0% of flight | p10-p50 (tied) |

*(tied)*: many public flights have exactly this value (for clipping and motor saturation, usually zero), so this flight sits with them.

### GPS delay

The GPS lags the IMU by **110 ms** (± 1.0); EKF2 fused this log's GPS samples 98 ms before they arrived, a shift the DroneCAN receiver's own time stamps set, not SENS_GNSSn_DELAY (+12 ms).

(PX4 v1.18 replaced EKF2_GPS_DELAY with a delay per receiver, SENS_GPS0_DELAY and SENS_GPS1_DELAY; PX4 main names them SENS_GNSS0_DELAY and SENS_GNSS1_DELAY. The delay above is read from the log's own sample times.)

EKF2's own GPS velocity innovations lean the other way (implied -7 ms), so the two methods disagree on this flight.

Within 50 ms of the setting: nothing to change.

## 53a8335a-3705-4d98-acff-5d21cae8e6de.ulg

PX4 v1.18.0-128, 5.5 min in the air, multicopter.

**2 flags**

- GPS jamming indicator median 107 (PX4 docs: should be around or below 40)
- battery warning level 3 in flight

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 1.3 m/s² (green) | p10-p25 |
| Vibration, 95th percentile | 1.8 m/s² | p10-p25 |
| Accelerometer clipping | 0 events (0.00 per min) | p10-p75 (tied) |
| GPS jamming indicator, median | 107 (docs: about 40 or below) | p90-p99 |
| GPS receiver jamming state, max | 1 (ok) |  |
| Satellites used, median | 32 | p75-p90 |
| eph, median | 0.16 m | p25-p50 |
| Battery warning, worst | emergency |  |
| Failsafe in flight | no |  |
| A motor at full command | 0.0% of flight | p50-p75 |

*(tied)*: many public flights have exactly this value (for clipping and motor saturation, usually zero), so this flight sits with them.

### GPS delay

The GPS lags the IMU by **190 ms** (± 2.4); this vehicle's SENS_GNSS0_DELAY is 110 ms, and EKF2 fused the GPS samples that much before they arrived (+80 ms).

(PX4 v1.18 replaced EKF2_GPS_DELAY with a delay per receiver, SENS_GPS0_DELAY and SENS_GPS1_DELAY; PX4 main names them SENS_GNSS0_DELAY and SENS_GNSS1_DELAY. The delay above is read from the log's own sample times.)

EKF2's own GPS velocity innovations lean the same way (implied +34 ms).

**Suggestion:** try SENS_GNSS0_DELAY = 190 as a starting value. Measure two or three flights first (one vehicle's flights agree within a few ms), and check the change with EKF2 log replay before flying it. On public logs, about 30% of vehicles are more than 50 ms off their setting.

On public PX4 logs, a delay this far off went with higher EKF2 GPS velocity test ratios while accelerating (median lift 0.43 vs 0.24: the ratio while accelerating was 2.7 times its steady-flight value, against 1.7 times on logs within 50 ms), and mismatched logs saw more GPS velocity rejections (20% of logs against 16%, a gap within chance on 93 and 163 logs). Source: px4-gps-lag, studies/mismatch-2026/GRADE.md.


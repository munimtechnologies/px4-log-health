# PX4 log health

3 logs, checked with px4-log-health checkup 0.3.0 by MV2 of Munim, Inc. (made with AI agents). A flag is a reason to look at the log in Flight Review, not a diagnosis.

## 1702bde1-d47f-45a3-b003-22b4f213c961.ulg

PX4 v1.11.3, 6.3 min in the air, multicopter.

**No flags.**

Notes:

- vibration not banded: PX4 v1.11.3 logs the metric in m/s (from delta velocities), so Flight Review's m/s^2 bands do not apply

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 0.010 m/s (PX4 v1.12 or older units; not banded) |  |
| Accelerometer clipping | 0 events (0.00 per min) | p75-p90 |
| GPS jamming indicator, median | 13 (docs: about 40 or below) | p25-p50 |
| Satellites used, median | 28 | p50-p75 |
| eph, median | 0.62 m | p50-p75 |
| Failsafe in flight | no |  |

### GPS delay

The GPS lags the IMU by **39 ms** (± 4.1); this vehicle's EKF2_GPS_DELAY is 110 ms (-71 ms).

EKF2's own GPS velocity innovations lean the same way (implied -42 ms).

**Suggestion:** try EKF2_GPS_DELAY = 39 as a starting value. Measure two or three flights first (one vehicle's flights agree within a few ms), and check the change with EKF2 log replay before flying it. On public logs, about 30% of vehicles are more than 50 ms off their setting.

## 04f407d5-40a5-417f-9a07-9884f784028b.ulg

PX4 v1.16.1-192, 4.0 min in the air, not a multicopter.

**No flags.**

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 2.9 m/s² (green) | p25-p50 |
| Vibration, 95th percentile | 5.7 m/s² | p25-p50 |
| Accelerometer clipping | 0 events (0.00 per min) | p75-p90 |
| GPS jamming indicator, median | 7 (docs: about 40 or below) | p10-p25 |
| GPS receiver jamming state, max | 1 (ok) |  |
| Satellites used, median | 32 | p75-p90 |
| eph, median | 0.37 m | p25-p50 |
| Battery warning, worst | none |  |
| Failsafe in flight | no |  |

### GPS delay

The GPS lags the IMU by **128 ms** (± 1.2); this vehicle's EKF2_GPS_DELAY is 110 ms (+18 ms).

EKF2's own GPS velocity innovations lean the same way (implied +19 ms).

Within 50 ms of the setting: nothing to change.

## 884a84f2-d491-4729-b3b4-49701c98b93e.ulg

PX4 v1.12.3-0, 5.2 min in the air, multicopter.

**No flags.**

Notes:

- vibration not banded: PX4 v1.12.3-0 logs the metric in m/s (from delta velocities), so Flight Review's m/s^2 bands do not apply

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 0.065 m/s (PX4 v1.12 or older units; not banded) |  |
| Accelerometer clipping | 0 events (0.00 per min) | p75-p90 |
| GPS receiver jamming state, max | 0 (unknown) |  |
| Satellites used, median | 28 | p50-p75 |
| eph, median | 1.64 m | p75-p90 |
| Battery warning, worst | none |  |
| Failsafe in flight | no |  |

### GPS delay

The GPS lags the IMU by **160 ms** (± 0.7); this vehicle's EKF2_GPS_DELAY is 110 ms (+50 ms).

EKF2's own GPS velocity innovations lean the same way (implied +23 ms).

Within 50 ms of the setting: nothing to change.


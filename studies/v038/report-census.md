# PX4 log health

2 logs, checked with px4-log-health checkup 0.3.8 by MV2 of Munim, Inc. (made with AI agents). A flag is a reason to look at the log in Flight Review, not a diagnosis.

## 0d86be6a-af24-415f-b499-c1d0de592050.ulg

PX4 v1.16.2, 8.5 min in the air, multicopter.

**No flags.**

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 4.0 m/s² (green) | p25-p50 |
| Vibration, 95th percentile | 8.6 m/s² | p50-p75 |
| Accelerometer clipping | 0 events (0.00 per min) | p10-p75 (tied) |
| GPS jamming indicator, median | 10 (docs: about 40 or below) | p25-p50 |
| GPS receiver jamming state, max | 1 (ok) |  |
| Satellites used, median | 30 | p50-p75 |
| eph, median | 1.07 m | p75-p90 |
| Battery warning, worst | none |  |
| Failsafe in flight | no |  |
| A motor at full command | 0.9% of flight | p75-p90 |

*(tied)*: many public flights have exactly this value (for clipping and motor saturation, usually zero), so this flight sits with them.

### GPS delay

The GPS lags the IMU by **211 ms** (± 0.6); this vehicle's EKF2_GPS_DELAY is 110 ms (+101 ms).

EKF2's own GPS velocity innovations lean the same way (implied +25 ms).

**Suggestion:** try EKF2_GPS_DELAY = 211 as a starting value. Measure two or three flights first (one vehicle's flights agree within a few ms), and check the change with EKF2 log replay before flying it. On public logs, about 30% of vehicles are more than 50 ms off their setting.

On public PX4 logs, a delay this far off went with higher EKF2 GPS velocity test ratios while accelerating (median lift 0.43 vs 0.24: the ratio while accelerating was 2.7 times its steady-flight value, against 1.7 times on logs within 50 ms), and mismatched logs saw more GPS velocity rejections (20% of logs against 16%, a gap within chance on 93 and 163 logs). Source: px4-gps-lag, studies/mismatch-2026/GRADE.md.

**u-blox dynamic model 6.** This receiver runs GPS_UBX_DYNMODEL = 6 (airborne <1g), which PX4 set for every multicopter up to v1.16. On public PX4 logs, model-6 u-blox logs lagged a median 46 ms more than model-7 logs from the same receiver class (244 vehicles), and about 4 in 10 were more than 50 ms past their delay setting. On the one vehicle flown with both (the logs on PX4 PR #24831), the model-6 flight lagged 104 ms more than the model-7 flight, and the extra lag behaved like heavier smoothing (a fitted filter time constant of about 110 ms against 20 ms), which no single GPS delay setting fully cancels. Across public logs too, model 6's extra lag behaved like filtering: within receiver classes, the fitted time constant was a median 110 ms longer than at model 7 (163 logs). If GPS lag matters on this vehicle, try GPS_UBX_DYNMODEL = 7 (<2g, PX4's default for multicopters since v1.17; it takes effect after a reboot) and measure again. Source: px4-gps-lag, studies/dynmodel-2026/GRADE.md, GRADE-paired.md, GRADE-mechanism.md and GRADE-tau-fleet.md.

### Compass

Compass barely moves with battery current: the field norm moves 2.7% over this flight's current range (21.9-31.0 A, r = 0.15); fleet place p25-p50 of 63 public PX4 flights.

## 10457c04-f8e9-425c-9ad9-35d404e95439.ulg

PX4 v1.15.0, 2.6 min in the air, multicopter.

**No flags.**

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 1.6 m/s² (green) | p10-p25 |
| Vibration, 95th percentile | 1.9 m/s² | p10-p25 |
| Accelerometer clipping | 0 events (0.00 per min) | p10-p75 (tied) |
| Battery warning, worst | none |  |
| Failsafe in flight | no |  |
| A motor at full command | 0.0% of flight | p10-p50 (tied) |

*(tied)*: many public flights have exactly this value (for clipping and motor saturation, usually zero), so this flight sits with them.

### GPS delay

Not measured: error: no vehicle_gps_position in the log. The lag needs a flight with some manoeuvring; hover-only flights usually can't be measured.

### Compass

**Flag:** Compass tracks battery current: the field norm moves 17.0% over this flight's current range (36.5-46.5 A, r = 0.75); fleet place p75-p90 of 63 public PX4 flights. Motor current is bending the compass, which hurts the heading estimate. Move the compass away from power wiring, or see PX4's Compass Power Compensation guide: https://docs.px4.io/main/en/advanced_config/compass_power_compensation


# PX4 log health

2 logs, checked with px4-log-health checkup 0.3.8 by MV2 of Munim, Inc. (made with AI agents). A flag is a reason to look at the log in Flight Review, not a diagnosis.

## log_5_2025-5-27-11-50-34.ulg

Health not read: under 60 s in the air.

### GPS delay

The GPS lags the IMU by **110.5 ms** (± 1.0); this vehicle's EKF2_GPS_DELAY is 160 ms (-49.5 ms).

EKF2's own GPS velocity innovations lean the same way (implied -38 ms).

Within 50 ms of the setting, but close to the 50 ms line (-49.5 ms): no change suggested; measure another flight to see which side it falls on.

### Compass

Compass barely moves with battery current: the field norm moves 4.3% over this flight's current range (8.8-23.7 A, r = 0.35); fleet place p50-p75 of 63 public PX4 flights.

## log_8_2025-5-27-12-55-02.ulg

Health not read: under 60 s in the air.

### GPS delay

The GPS lags the IMU by **214 ms** (± 2.1); this vehicle's EKF2_GPS_DELAY is 160 ms (+54 ms).

EKF2's own GPS velocity innovations lean the same way (implied +29 ms).

**Suggestion:** try EKF2_GPS_DELAY = 214 as a starting value. Measure two or three flights first (one vehicle's flights agree within a few ms), and check the change with EKF2 log replay before flying it. On public logs, about 30% of vehicles are more than 50 ms off their setting.

On public PX4 logs, a delay this far off went with higher EKF2 GPS velocity test ratios while accelerating (median lift 0.43 vs 0.24: the ratio while accelerating was 2.7 times its steady-flight value, against 1.7 times on logs within 50 ms), and mismatched logs saw more GPS velocity rejections (20% of logs against 16%, a gap within chance on 93 and 163 logs). Source: px4-gps-lag, studies/mismatch-2026/GRADE.md.

**u-blox dynamic model 6.** This receiver runs GPS_UBX_DYNMODEL = 6 (airborne <1g), which PX4 set for every multicopter up to v1.16. On public PX4 logs, model-6 u-blox logs lagged a median 46 ms more than model-7 logs from the same receiver class (244 vehicles), and about 4 in 10 were more than 50 ms past their delay setting. On the one vehicle flown with both (the logs on PX4 PR #24831), the model-6 flight lagged 104 ms more than the model-7 flight, and the extra lag behaved like heavier smoothing (a fitted filter time constant of about 110 ms against 20 ms), which no single GPS delay setting fully cancels. Across public logs too, model 6's extra lag behaved like filtering: within receiver classes, the fitted time constant was a median 110 ms longer than at model 7 (163 logs). If GPS lag matters on this vehicle, try GPS_UBX_DYNMODEL = 7 (<2g, PX4's default for multicopters since v1.17; it takes effect after a reboot) and measure again. Source: px4-gps-lag, studies/dynmodel-2026/GRADE.md, GRADE-paired.md, GRADE-mechanism.md and GRADE-tau-fleet.md.

### Compass

Compass barely moves with battery current: the field norm moves 1.9% over this flight's current range (8.7-21.2 A, r = 0.18); fleet place p25-p50 of 63 public PX4 flights.


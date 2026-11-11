# PX4 log health

1 log, checked with px4-log-health checkup 0.3.10 by MV2 of Munim, Inc. (made with AI agents). A flag is a reason to look at the log in Flight Review, not a diagnosis.

## 0d352749-1251-4136-821d-98f2df249102.ulg

PX4 v1.17.0, 13.2 min in the air, multicopter.

**4 flags**

- vibration red (median 26.8 m/s^2; Flight Review's plot turns orange at 4.9 and red at 9.8)
- accelerometer clipped 1458 times in flight
- GPS receiver reported spoofing (state 2)
- battery warning level 3 in flight

| Figure | This flight | Among 208 public PX4 flights (fleet-health-2026) |
|---|---|---|
| Vibration, median | 26.8 m/s² (red) | p90-p99 |
| Vibration, 95th percentile | 37.8 m/s² | p90-p99 |
| Accelerometer clipping | 1458 events (110.18 per min) | p90-p99 |
| GPS jamming indicator, median | 28 (docs: about 40 or below) | p50-p75 |
| GPS receiver jamming state, max | 1 (ok) |  |
| Satellites used, median | 31 | p50-p75 |
| eph, median | 0.27 m | p25-p50 |
| Battery warning, worst | emergency |  |
| Failsafe in flight | no |  |
| A motor at full command | 0.0% of flight | p10-p50 (tied) |

*(tied)*: many public flights have exactly this value (for clipping and motor saturation, usually zero), so this flight sits with them.

### GPS delay

Not measured: IMU explains too little of the GPS velocity change. The lag needs a flight with some manoeuvring; hover-only flights usually can't be measured.

**u-blox dynamic model 6.** This receiver runs GPS_UBX_DYNMODEL = 6 (airborne <1g), which PX4 set for every multicopter up to v1.16. On public PX4 logs, model-6 u-blox logs lagged a median 46 ms more than model-7 logs from the same receiver class (244 vehicles), and about 4 in 10 were more than 50 ms past their delay setting. On the one vehicle flown with both (the logs on PX4 PR #24831), the model-6 flight lagged 104 ms more than the model-7 flight, and the extra lag behaved like heavier smoothing (a fitted filter time constant of about 110 ms against 20 ms), which no single GPS delay setting fully cancels. Across public logs too, model 6's extra lag behaved like filtering: within receiver classes, the fitted time constant was a median 110 ms longer than at model 7 (163 logs). If GPS lag matters on this vehicle, try GPS_UBX_DYNMODEL = 7 (<2g, PX4's default for multicopters since v1.17; it takes effect after a reboot) and measure again. Source: px4-gps-lag, studies/dynmodel-2026/GRADE.md, GRADE-paired.md, GRADE-mechanism.md and GRADE-tau-fleet.md.

### Compass

Compass barely moves with battery current: the field norm moves 7.3% over this flight's current range (6.5-20.0 A, r = 0.08); fleet place p50-p75 of 63 public PX4 flights.

### Battery

**Note:** PX4's battery-resistance estimate was 0 for the whole second half of the flight (it was already 0 when the vehicle took off) while BAT1_R_INTERNAL is -1 (use the estimate), so PX4 did not correct the voltage-based state of charge for the voltage drop under load: it reads low under load and recovers after landing. On public PX4 flights this happened on 9 of 53 that use the estimate. A wrong current sensor scale or offset can also cause it. In flight the estimate barely updates, and on 90 of 211 public PX4 flights it had already settled at the first logged sample (studies/battery-r-2026/GRADE-replay.md), so a log that starts at arming may not show why. To see why, log one flight from boot (SDLOG_MODE 2) and add the line `battery_status 0` to etc/logging/logger_topics.txt on the SD card (PX4 then logs that topic at full rate, on top of its default topics). See PX4's battery estimation guide: https://docs.px4.io/main/en/config/battery


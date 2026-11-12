# mcloss-2026: in-flight manual-control loss in public PX4 logs (in preparation)

Status, 2026-11-12: **code only, no data read.** This folder holds `mcloss_core.py`, the analysis core of a
planned census, and its synthetic tests. The pre-registration (`PLAN.md`, with the sample, the exact
measures and the predictions) will be published here before any flight log is downloaded. Until then
nothing in this folder is a result.

## Why

[PX4/flight-review-rs](https://github.com/PX4/flight-review-rs) (Flight Review v2) has an `rc_loss` analyzer
that reads only the `input_rc` topic. On flight-review-rs issue
[#56](https://github.com/PX4/flight-review-rs/issues/56) a maintainer noted that on a rover driven with a
joystick over MAVLink during v1.18 release testing, the RC-loss failsafes were tripped by
`failsafe_flags.manual_control_signal_lost`, while `rc_loss` reported nothing useful because it only watches
`input_rc`, and suggested this gap be its own issue. The census will measure, on public logs, how often manual
control is lost in the air, over which link (RC or MAVLink), and how often an `input_rc`-only check misses it,
and will list candidate logs that could become a real test fixture.

## What the core computes (per log)

- armed time (`vehicle_status.arming_state == 2`) and airborne time (armed and
  `vehicle_land_detected.landed` false)
- whether manual control was ever present while armed (`link`: used / never / no_flag / not_armed)
- loss events: a false-to-true change of `failsafe_flags.manual_control_signal_lost` that starts while
  airborne and lasts at least 0.5 s (the minimum the `rc_loss` analyzer uses)
- for each event: the link in use just before it (`manual_control_setpoint.data_source`: 1 = RC,
  2-7 = MAVLink), whether `input_rc.rc_lost` or `rc_failsafe` was true within 1 s of it, and the
  navigation state 2 s after it

No positions, GPS tracks or log descriptions are read or written.

Run the tests: `python -m unittest -v test_mcloss_core`

Made by MV2 of Munim, Inc. (mv2@munimtech.com), with AI agents working in his name. Code Apache-2.0.

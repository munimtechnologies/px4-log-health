# mcloss-2026: in-flight manual-control loss in public PX4 logs (pre-registered, no data read yet)

Status, 2026-11-13: **plan and code published, no data read.** [PLAN.md](PLAN.md) is the pre-registration:
the sample, the exact measures and four graded predictions. The census job checks that PLAN.md and both
Python files on main are the copies it runs before it downloads anything. Until `GRADE.md` appears here,
nothing in this folder is a result.

## Why

[PX4/flight-review-rs](https://github.com/PX4/flight-review-rs) (Flight Review v2) has an `rc_loss` analyzer
that reads only the `input_rc` topic. On flight-review-rs issue
[#56](https://github.com/PX4/flight-review-rs/issues/56) the author noted that on a rover driven with a
joystick over MAVLink during v1.18 release testing, the RC-loss failsafes were tripped by
`failsafe_flags.manual_control_signal_lost`, while `rc_loss` reported nothing useful because it only watches
`input_rc`, and suggested this gap be its own issue. The census measures, on public logs, how often manual
control is lost in the air, over which link (RC or MAVLink), and how often an `input_rc`-only check misses it,
and lists candidate logs that could become a real test fixture.

## Files

- `PLAN.md`: the pre-registration (never edited; corrections go in dated `NOTE-*.md` files).
- `mcloss_core.py`: per-log analysis on plain lists (no numpy, no pyulog).
- `mcloss_census.py`: the census driver (Flight Review listing, sampling, download, duplicate check, pyulog
  extraction, grading).
- `test_mcloss_core.py`, `test_mcloss_census.py`: synthetic tests (no logs, no network).

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

Run the tests: `python -m unittest -v test_mcloss_core test_mcloss_census`

Run the census (needs `numpy pyulog requests ijson`; downloads up to 360 public logs, 6 s apart):
`python mcloss_census.py /out`

Made by MV2 of Munim, Inc. (mv2@munimtech.com), with AI agents working in his name. Code Apache-2.0; text and
data CC BY 4.0.

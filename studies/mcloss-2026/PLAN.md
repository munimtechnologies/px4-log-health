# mcloss-2026 pre-registration: in-flight manual-control loss in public PX4 logs

Written 2026-11-13 by AI agents working in MV2's name (MV2, Munim, Inc., mv2@munimtech.com), before any flight
log was downloaded for this study. The census job (`mcloss_census.py` 0.1.0, core `mcloss_core.py` 0.1.0) runs
its synthetic tests first, then refuses to read data until this file, `mcloss_census.py` and `mcloss_core.py`
on main are byte-identical (CRLF/LF aside) to the copies it runs. This file is never edited after publication;
corrections go into a dated `NOTE-*.md` written before the results they affect.

## Why

[PX4/flight-review-rs](https://github.com/PX4/flight-review-rs) (Flight Review v2) has an `rc_loss` analyzer
that reads `input_rc` (`rc_lost`, `rc_failsafe`). Its source says no ULog in its corpus shows RC signal loss.
On flight-review-rs issue [#56](https://github.com/PX4/flight-review-rs/issues/56) (2026-09-28) the author
reported that on a rover driven with a joystick over MAVLink the RC-loss failsafes came from
`failsafe_flags.manual_control_signal_lost`, which the analyzer never reads, and that this is worth a separate
issue. As of 2026-11-13 nobody has filed it. No published figure says how often public PX4 flights lose manual
control in the air, over which link, or how often an `input_rc`-only check would see it.

## Questions

- Q1. In a random sample of public real-hardware PX4 v1.14+ logs, what share of flights that used manual
  control have at least one in-air manual-control loss event?
- Q2. Of logs with an event, what share were on MAVLink manual control (not RC) just before the first event?
- Q3. For those events, does `input_rc` itself show the loss (phase 1), and does flight-review-rs's `rc_loss`
  analyzer, run as built, report it (phase 2)?
- Q4. A list of every log with an event (log id, link, duration, what the vehicle did next), so maintainers can
  pick a real fixture.

## Population and samples

- Source: `https://review.px4.io/dbinfo`, streamed once by the job.
- Eligible: not SITL (`sys_hw`), not a simulation (`type`), not CI (`source`); a log id; a real vehicle UUID
  (not empty, at least 8 characters, not only 0/f/-); `duration_s` >= 120; `ver_sw_release` v1.14 or newer
  (`failsafe_flags` exists from v1.14). Any vehicle type.
- Order key: sha256 of the log id (hex). One log per vehicle per sample: the vehicle's eligible log with the
  smallest key.
- **R (random):** that pick for every vehicle, in order of key. Q1 uses R only.
- **E (enriched):** the same, among logs whose Flight Review mode list (`flight_modes`) holds a manual mode
  (nav_state 0 MANUAL, 1 ALTCTL, 2 POSCTL, 6 POSITION_SLOW, 10 ACRO, 15 STAB) and also 5 AUTO_RTL,
  12 DESCEND or 18 AUTO_LAND. E only adds events for Q2-Q4.
- Walk: R, R, R, E, repeated. A log reached by both samples is downloaded once and counts in each only when that
  sample's turn reaches it. Stop at 260 counted R logs and 80 counted E logs, 360 downloads or 240 minutes.
- Downloads: 6 s apart, 50 MB or less (gzip unpacked), stop on HTTP 403, 429 or 444. No other Flight Review
  download job of MV2's runs at the same time.
- **Duplicates:** a file whose sha256 equals an earlier file of this job is recorded as a duplicate and never
  counted (compare-2026 found 22 of 40 "null" pairs were one file uploaded twice).
- Counted log: downloaded, not a duplicate, parsed by pyulog without error.

## Per-log measures (mcloss_core.py)

Topics (lowest multi_id): `vehicle_status` (arming_state, nav_state), `vehicle_land_detected` (landed),
`failsafe_flags` (manual_control_signal_lost), `manual_control_setpoint` (data_source), `input_rc` (rc_lost,
rc_failsafe); parameters COM_RC_IN_MODE, NAV_RCL_ACT, COM_RC_LOSS_T, COM_RCL_EXCEPT. Each topic is a step
function; out-of-order timestamps are sorted and the topic is noted.

- armed: arming_state == 2; airborne: armed and landed false.
- link: `used` if manual_control_signal_lost was false at any armed time; `never`, `no_flag`, `not_armed`
  otherwise. Status `missing_topics` if vehicle_status, vehicle_land_detected or failsafe_flags is absent.
- **Loss event:** a false-to-true change of manual_control_signal_lost whose onset is airborne and whose true
  run lasts >= 0.5 s (the rc_loss analyzer's minimum). A flag true from the topic's first sample is not an event.
- source: last data_source strictly before the onset, at most 5 s old: 1 = rc, 2-7 = mavlink, 0 = unknown,
  nothing = none.
- rc_agrees: `input_rc.rc_lost` or `rc_failsafe` true at some time in [onset - 1 s, end + 1 s]; none if
  `input_rc` is absent.
- nav_after: nav_state 2 s after the onset.
- No position, GPS track or log description is read or written.

## Predictions

- **P1:** among counted R logs with status ok, link `used` and >= 10 s airborne, the share with at least one
  event is between 1% and 10% inclusive. Too few if fewer than 150 such logs.
- **P2:** among logs with an event (R and E), using each log's first event, at least 20% have source mavlink
  out of those with source rc or mavlink. Too few if fewer than 10.
- **P3a:** of those mavlink first events, at least 80% have rc_agrees not true (false or no input_rc). Too few
  if fewer than 6.
- **P4a:** of rc first events with input_rc logged, at least 70% have rc_agrees true. Too few if fewer than 6.
- **Phase 2 (P3b, P4b):** flight-review-rs at a pinned commit, built and run as is, on every log with an event:
  P3b the share of mavlink first events with no `rc_loss` finding is at least 80%; P4b the share of rc first
  events with input_rc where `rc_loss` reports a finding is at least 70%; too few under 6. How the CLI is
  called and what counts as a finding will be fixed in a dated `NOTE-frrs.md` before phase 2 runs.
- Descriptive, not graded: link and source mix in R, COM_RC_IN_MODE, input_rc presence, nav_after, cleared vs
  open at log end, events by vehicle type.

## Limits stated in advance

Public uploads are not a random sample of PX4 flights; logs over 50 MB are skipped; one log per vehicle; the
flag reflects PX4's own loss timeout (COM_RC_LOSS_T) and is not a radio measurement; MAVLink manual control on
a vehicle that also has RC may be a GCS joystick or a companion computer, and the log cannot tell which.

## After the result

Grade in `GRADE.md` here, held or failed as written. If events exist, open one issue on PX4/flight-review-rs
with the numbers and candidate log ids, and offer a separate `manual_control_loss` analyzer that reads
`failsafe_flags.manual_control_signal_lost`. If P3a fails, the gap is smaller than #56 suggests, and the issue
says so. Code Apache-2.0; text and data CC BY 4.0.

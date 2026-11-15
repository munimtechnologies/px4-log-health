# NOTE 2026-11-15: BAT1 voltage and current move together, sample to sample (exploratory)

Study: battery-r-2026 (px4-log-health). Status: **exploratory, post hoc, computed by hand**. This note does not grade
PLAN-trace.md (that needs from-boot ULog files, which were not shared) and does not change any frozen plan.
What the queued full-file run should show is pre-registered separately in PLAN-commonmode.md, written before that run.

Text: CC BY 4.0. Made with AI agents working in the name of MV2, Munim, Inc. (mv2@munimtech.com). No human has
checked these numbers.

## Data

The original poster of PX4 forum topic
[49724](https://discuss.px4.io/t/battery-resistance-estimate-reaches-zero-and-persists-under-load-fmuv6c-bat1-r-internal-1/49724)
shared, by email on 2026-11-14, CSV exports from a Holybro X500 V2 with a Pixhawk 6C (FMUv6C). BAT1 uses the analog
power-module path (voltage on ADC channel 8, current on channel 4), with PX4 1.16-derived firmware and
`BAT1_R_INTERNAL=-1`. Two flights from one powered session were logged at about 100 Hz: battery_status (with
`voltage_prediction`, `prediction_error`, `estimation_covariance_norm`), the raw BAT1 ADC counts and the four MAIN
PWM outputs. Times are relative to the first `landed=false` report. The raw files are not published here. Only
derived numbers are quoted, as agreed with the owner.

Conversion checked on sampled rows: pack volts = counts x 3.3/65536 x 18.1 (0.91141 mV per count) and amps =
counts x 3.3/65536 x 36.3675 (1.83125 mA per count), with no current offset. For example, 17783 counts = 16.2076 V
and 1197 counts = 2.1920 A, as logged.

## What was read

- Flight A: every row from -0.83 s to +0.30 s, plus 100 ms spot rows from -5.9 s to -1.0 s and short spot windows at
  +1.0, +3.0, +10.0 and +20.0 s.
- Flight B: every row from -0.60 s to +0.60 s.
- In total, roughly 300 of the 18,650 rows at 100 Hz were read. The 5 Hz trace from 9 Nov was not read beyond its first 6 rows.

## Timeline of flight A around spool-up

- Armed about 5.51 s before the marker. Outputs go from 1000 to about 1100 us by -5.0 s, with idle current at 0-0.3 A.
- Throttle-up starts between -0.805 and -0.736 s, when the outputs leave about 1100 us. The current leaves zero at
  -0.738 s (0.27 A).
- The current then rises smoothly at about 5 A/s: about 1.1 A at -0.30 s, 2.2 A at -0.05 s and 4.3 A at +0.29 s.
  The outputs ramp by about 3 us per 10 ms. There is no sharp load step in this window. The marker comes at only
  about 2.3 A, while hover at +3 s draws about 12.5 A.
- The published estimate was 6.3-12.2 mOhm/cell for most of the ramp. It then fell: 2.71 (-138 ms), 4.18 (-88),
  3.21 (-78), 0.87 (-58) and 0 (-48 ms), and stayed at zero.

## How the estimator reached zero (PX4 battery.cpp, revision 6ea35391)

An update is accepted only if the new covariance norm is smaller than the current one. That test depends only on
the current samples. When an update is rejected, the OCV state is re-anchored to that single sample:
`_RLS_est(0) = V + R*I`.

- Between -0.64 s and -0.04 s the logged covariance norm fell 16 times (16 accepted updates). In 15 of the 16, the
  measured current was at a new running high.
- At -68 ms the update was rejected (the norm was unchanged at 0.4509), so the OCV state was re-anchored to that
  sample. Check: 16.0517 V + 4 x 0.0032117 Ohm x 1.7470 A = 16.0742 V. The prediction for the next sample is then
  16.0742 - 0.012847 x 2.0199 = 16.0482 V, which matches the logged `voltage_prediction` (16.04820).
- The next two samples (-58 and -48 ms) were both accepted. In both, voltage and current rose together (raw counts
  +70/+149, then +101/+94). The prediction errors were +0.067 and +0.147 V, which pushed R down to 0.87 mOhm/cell
  and then below zero. The topic's replay found a signed candidate of about -4.27 mOhm/cell.
- Flight B: the norm stayed at 0.06335 from the first sample through +446 ms (every update rejected). The stored
  signed R implied by consecutive predictions was about -10 mOhm per pack (about -2.5 mOhm/cell). One accepted
  update at +456 ms moved it to +2.15 mOhm/cell. That step went the right way in raw counts (-265/+250).

## The pattern: same-sign steps in the raw counts

All numbers below are for steps between consecutive samples. A step is "wrong-way" when the voltage moved in the
same direction as the current, for steps with |dI| >= 0.05 A (27 counts).

| Window | Steps | Wrong-way | Other |
| --- | ---: | ---: | --- |
| Flight A, -0.30 to +0.30 s | 52 | 34 (65%) | corr(dV counts, dI counts) = **+0.46** over 59 steps; slope of dV on dI = +0.27 counts/count (+0.13 V/A) |
| Flight B, -0.60 to +0.60 s | 102 | 60 (59%) | the 10 largest voltage jumps all came with a same-sign current jump; median ratio dI/dV = about 0.9 in counts |
| Flight A spot windows (+3, +10, +20 s) | 7 | 6 | at +3 s: -350/-264, +246/+267, +205/+239, -478/-1116 counts |

Of the 10 largest current steps in each window, 9 of 10 were wrong-way in both flights.

A real battery gives dV = -R dI, with R around 5-10 mOhm/cell, that is, 20-40 mOhm per 4S pack. That is a small
right-way slope. What the counts show instead is a common component of similar size on both channels. Here is how
the obvious explanations compare:

- **Additive disturbance common to both ADC inputs** (for example, a ground offset between the power module and the
  FMU's ADC ground). Equal millivolts at both pins give equal counts: ratio about 1. **This fits** the largest voltage
  jumps in flight B. In pack units, such a spike looks like dV/dI = +18.1/36.37 = +0.50 V/A, which is a "resistance"
  of about -0.5 Ohm per pack (-124 mOhm/cell) in a one-step update.
- **ADC reference error** (multiplicative). This moves each channel in proportion to its reading. The ratio would be
  about 17,600 : 1,000-3,000 counts, that is, mostly voltage. **That does not fit.**
- **A fixed time offset between the channels** (the earlier simulation's 20 ms current lag). The strongest relation
  would then sit at a nonzero shift and would go the right way. In these windows, the co-movement is within the same
  sample. A lag cannot be ruled out from these windows, but it is not needed to explain them. PLAN-commonmode.md
  tests this on whole files.

A rough size, using flight A's window and a model where the shared part is additive: the covariance of the count
steps (about 6,400 counts squared per step) implies a common component of about 80 counts (4 mV at the pins) per
step. That would be about 0.07 V on the pack voltage and 0.15 A on the current at the same time.

## What longer averages show

Six 100 ms block means after takeoff in flight B (current 2.4 -> 5.4 A) give a slope of -0.034 V/A, about
**8.5 mOhm/cell**, which is plausible for a 4S LiPo. Flight A's six blocks before takeoff (1.2 -> 3.7 A) are flat
(+0.001 V/A): the load change is too small against the noise to say anything. If the disturbance is mostly
sample-to-sample, averaging V and I before the estimator, or requiring an excitation well above it, would remove most
of it. This was not tested.

## Limits

- One vehicle, one session, an analog power module of unconfirmed model, and no independent instruments.
- Hand arithmetic on short windows. The full-file numbers come from trace_timing.py 0.1.0 (published 2026-11-14,
  before the data were read), which is queued.
- The 15-of-16 running-high pattern describes the gate's behaviour on a ramp, and the bias it implies is only
  suggestive. At the 16 accepted samples, the voltage residual against the 4 neighbouring samples was positive in 10
  of 16, with a mean of +0.018 V (about 1.4 standard errors).
- This supersedes, for this vehicle, the "current lags voltage" mechanism suggested in GRADE-sim.md and PLAN-skew.md.
  Those files stay frozen.

## Checks that need no instruments

1. If the log has `system_power.voltage5v_v` at a useful rate (on the FMU's own ADC), do its sample-to-sample
   changes move with the BAT1 current counts? If they do, the disturbance is at the FMU's ADC ground. If they don't,
   it is upstream, in the power module or cable.
2. Do the same analysis with propellers off and the motors at several fixed throttles (ESC current present, no
   flight). If the wrong-way share rises with motor current, that points to switching or ground-return coupling.

# PLAN-commonmode: do BAT1 voltage and current steps move together across whole files? (pre-registered)

Study: battery-r-2026 (px4-log-health). Written 2026-11-15, before the job below has produced any output. Background
is in NOTE-commonmode-2026-11-15.md: hand arithmetic on about 300 of the 18,650 rows at 100 Hz suggested that, on this
vehicle, sample-to-sample changes in BAT1 voltage and current move in the same direction. A real battery's
internal resistance would make them move in opposite directions.
Text: CC BY 4.0. Made with AI agents working in the name of MV2, Munim, Inc. (mv2@munimtech.com).

## Data and code

- Data: the three `*-battery.csv` files shared privately by the original poster of PX4 forum topic 49724: the 5 Hz
  trace from 9 Nov and flights A and B from 14 Nov at 100 Hz. Raw files stay private; only the numbers below are
  published.
- Code: `trace_timing.py` 0.1.0 (this folder, published 2026-11-14 before any of the shared data was read), unchanged.
  The job is `battr-trace-timing-v1`, already queued. If it fails for reasons other than the code, re-filing it
  unchanged under a new version suffix is allowed. Changing the code voids this plan.
- Quantities read from `trace-timing.json`, under `files[<name>].report`:
  - `lag_all.corr["0"]`: the Pearson correlation between dI[m] and -dV[m] over consecutive samples. A negative value
    means voltage and current steps move in the same direction.
  - `lag_all.corr[L]` for L = -10..10.
  - `wrong_way_share` = wrong_way / of_changes_ge_0_05a.

## What has already been seen (disclosed)

- Flight A: about 115 consecutive rows around takeoff and about 70 spot rows. Flight B: 120 consecutive rows. The
  hand-computed correlation in counts was +0.46 for flight A over -0.30..+0.30 s, which corresponds to
  `corr["0"]` of about -0.46. The wrong-way shares were 65% (flight A window) and 59% (flight B window).
- 9 Nov trace: only its first 6 rows, all at zero current. **C1 below is the only fully out-of-sample prediction.**

## Predictions

- **C1 (9 Nov, 5 Hz, unseen):** `corr["0"] < 0` and `wrong_way_share > 0.50`.
- **C2 (14 Nov flights A and B, each):** `corr["0"] <= -0.20` and `wrong_way_share > 0.55`.
- **C3 (each of the three files):** `corr["0"]` is the smallest (most negative) of the 21 values `corr[-10..10]`.
  That is, the same-direction movement happens within a single sample, with no shift between the channels.

## Grading

- Each prediction is graded per file as held or failed. Every number is reported, whatever the result. A file that
  the job skips, or that has fewer than 20 rows with both voltage and current, is reported as ungraded, not as failed.
- If C1 fails while C2 and C3 hold, the pattern is reported as seen only in the 14 Nov session.
- If C3 fails because a nonzero shift is more negative than shift 0, that shift and its value are reported as
  evidence of a timing offset between the channels.
- None of this establishes a physical cause. A held C1-C3 is consistent with an additive disturbance common to
  both ADC inputs, as described in the NOTE. The checks that could separate the causes (the FMU's 5 V rail and a
  propeller-off throttle sweep) need new data from the pilot.
- The grade is written to GRADE-commonmode.md.

# PLAN-apcompare: would ArduPilot's resistance method have stayed at zero on the same flight?

Study: battery-r-2026 (px4-log-health). Status: **pre-registered on 2026-11-16, before any job has run this
analysis on the shared data**. This file is frozen once published; corrections go in a dated NOTE before results.

Text: CC BY 4.0. Code (`apcompare.py`, `run_apcompare.py`, `test_apcompare.py`): Apache-2.0.
Made with AI agents working in the name of MV2, Munim, Inc. (mv2@munimtech.com). No human has checked this plan.

## Why

On flight A of the 100 Hz log shared in PX4 forum topic
[49724](https://discuss.px4.io/t/battery-resistance-estimate-reaches-zero-and-persists-under-load-fmuv6c-bat1-r-internal-1/49724),
PX4 published an internal-resistance estimate of 0 in all 3,685 airborne samples (NOTE-gate-2026-11-15.md). Two
things combine: the signed state went negative around spool-up (NOTE-commonmode-2026-11-15.md: voltage and current
steps that move together), and the voltage-blind record gate then froze it (NOTE-gate).

ArduPilot estimates the same quantity with a different design. If a maintainer asks "what should the estimator
do instead?", one useful data point is what ArduPilot's method does on exactly these samples. As far as we found,
nobody has run the two autopilots' battery-resistance methods side by side on one log.

## The two designs (source read on 2026-11-16)

PX4 (`src/lib/battery/battery.cpp`, `main`): recursive least squares on `V = OCV - R*I`, forgetting factor 0.95,
an update is kept only if the covariance norm sets a new low (the voltage never enters that test), the signed state
may go negative and is clamped to 0 only when published, and a rejected sample leaves `R` untouched.

ArduPilot (`libraries/AP_BattMonitor/AP_BattMonitor_Backend.cpp`, `update_resistance_estimate()`; constants
`AP_BATT_MONITOR_RES_EST_TC_1 0.5f` and `AP_BATT_MONITOR_RES_EST_TC_2 0.1f` in `AP_BattMonitor.h`):

- each call forms a candidate `r = -(V - V_filt) / (I - I_filt)` against voltage and current low-passed with a
  0.5 s time constant;
- a candidate is blended in only if it is positive, with weight `min(1, 0.1 * |I - I_filt| / I_max)`;
- the result is capped by `(V_ref - V) / (I - I_ref)`, where `V_ref` is the highest voltage seen (with its
  current);
- calls without positive current, or with a current equal to the filtered one, change nothing.
- `AP_BattMonitor::read()` calls it right after each backend read. ArduCopter schedules that read in
  `update_batt_compass` at 10 Hz, and an analog backend reads the ADC's averaged voltage.

Consequences that follow from the code alone (no data needed): ArduPilot's estimate can never be negative; once
positive it can never return to 0; it has no record gate, so it never freezes; and a deviation where voltage and
current move together gives a negative candidate, which is dropped. The other side of the trade: dropping every
non-positive candidate biases the estimate upward when the channel is noisy. Only the cap, a sag slope measured
from the highest voltage seen, limits that bias.

## Method (frozen)

`apcompare.py` 0.1.0 re-implements that method for analysis (float64; ArduPilot uses float32). State starts at 0.
The millisecond clock is 10,000 ms at the file's first row, then whole milliseconds elapsed (so the first call's
filter gain is clamped at 0.5, as after a normal boot). Each flight file starts from a fresh state. 4 cells.

- **Variant M (main):** non-overlapping 10-row blocks from the first row (a short last block is dropped), mean
  voltage and current per block, fed at the block's last timestamp. This stands in for ArduPilot's 10 Hz call on
  averaged readings.
- **Variant S (sensitivity):** every logged row at 100 Hz.
- **Airborne:** `landed_last_reported == 0` (for a block, the block's last row).
- Discard share = discarded / (discarded + blended) calls among airborne calls.

## What was seen before writing this

Flight A rows 1-6 and 10450-10459 (around takeoff), the NOTE-gate and NOTE-commonmode numbers, and three counts from
pattern searches: 3,685 rows with `landed_last_reported == 0`, all with a logged PX4 estimate of exactly 0; 581
pre-takeoff rows with positive current; 552 armed pre-takeoff rows. Flight B was not opened for this plan.

## Predictions

- **D1 (data check):** flight A has 3,685 airborne rows and PX4 logged 0 in all of them.
- **A1:** in M, flight A's estimate becomes positive before the first airborne block.
- **A2:** in M, flight A's last airborne value is between 3 and 20 mOhm per cell. (For reference: 100 ms block
  means in flight B gave a slope of about 8.5 mOhm/cell, NOTE-commonmode.)
- **A3:** in M, the median airborne value in flight A is between 4.25 and 17 mOhm per cell (within a factor of 2
  of 8.5).
- **A4:** in S, at least 40% of flight A's airborne candidates are non-positive and dropped.
- **A5:** in S, the airborne discard share is higher than in M (averaging removes some of the co-moving jitter).
- **B1:** in M, flight B's last airborne value is between 3 and 20 mOhm per cell. (PX4 ended flight B at 4.25.)

`run_apcompare.py` writes every input number of the grade to `apcompare.json`: outcome counts, first positive
time, discard shares, cap hits, median, min, max, final and +10/+20/+30 s values. GRADE-apcompare.md will report
each prediction as held or missed, with every number, misses included.

## Limits

This runs ArduPilot's method, not ArduPilot's firmware. The block means only approximate ArduPilot's ADC
averaging: the inputs are PX4's logged per-call values from an FMUv6C analog power module. One vehicle, one power
session. A positive ArduPilot-method value would not show the true resistance. It would only show that this design
does not lock at zero on these samples. The raw log stays private; only derived numbers are published.

# NOTE 2026-11-16: where the estimator's 2 s reset window ends in flight A, and what it means for PLAN-fwmatch

Study: battery-r-2026 (px4-log-health). Status: **exploratory; hand reading of logged rows, written before
PLAN-fwmatch has been run**. It does not grade any plan and does not change any frozen file.

Text: CC BY 4.0. Made with AI agents working in the name of MV2, Munim, Inc. (mv2@munimtech.com).
No human has checked these numbers.

## Why this note

PLAN-fwmatch.md seeds its firmware replay at "the last row whose logged norm equals the initial 6.01331854
(the last reset)". Reading the first update after the reset window by hand shows that this rule finds the
wrong row in flight A: the logged norm cannot tell a reset from an idle sample. This note records that before
the job runs, with the expected effect on each prediction, so the grade can be read honestly.

## What the log shows

Firmware: [battery.cpp at 6ea35391](https://github.com/PX4/PX4-Autopilot/blob/6ea3539157ca358c70a515878b77077af7d4611d/src/lib/battery/battery.cpp).
While the battery has been connected for less than 2 s, every call resets the estimator
(`_RLS_est = [voltage_v, R_DEFAULT * n]`, `P = diag(1.5 n, 0.1 n)`), and the update runs only when
`current_a > FLT_EPSILON`. `voltage_prediction` is computed from the stored state before each update.

Flight A (4S, 100 Hz from power-on). Before -102.0 s (the first 2.5 s of the log) only six rows have current:

| Row | Time from takeoff (s) | Current (A) | Logged norm | Logged prediction | Reading |
| --- | --- | --- | --- | --- | --- |
| 5 | -104.498 | 0.00549 | 0.960527122 | 16.2001572 | reset + update in the same call |
| 24 | -104.308 | 0.06592 | 0.962290764 | 16.2253799 | reset + update in the same call |
| 110 | -103.448 | 0.03663 | 0.961062968 | 16.3198414 | reset + update in the same call |
| 161 | -102.938 | 0.01282 | 0.960582018 | 16.3175831 | reset + update in the same call |
| 191 | -102.638 | 0.02014 | 0.960680366 | 16.2545509 | update from an earlier reset |
| 247 | -102.078 | 0.04212 | 0.650948107 | 16.2387218 | second update, no reset |

- Rows 5, 24, 110 and 161: the prediction equals that row's own voltage minus `0.02 * I` (to float32
  spacing), and the norm equals the one-step closed form from `diag(6, 0.4)` with lambda 0.95. For example, row 24 gives
  0.9622909 (logged 0.962290764). The next row's norm is back at 6.01331854. So the reset ran in each of these
  calls, before the update.
- Row 191: the norm again matches one step from `diag(6, 0.4)` (0.9606806 by hand, logged 0.960680366),
  but the prediction does **not** match the row's own voltage (16.2367249 - 0.02 * 0.0201437 = 16.2363), nor
  row 190's (16.25313 -> 16.2527271, 1.82 mV off). It matches the voltage of **row 176**, 16.2549534:
  16.2549534 - 0.000402875 = 16.2545505 (logged 16.2545509). The logged resistance confirms it. With that
  seed, the update gives R = (0.02 + 0.00115933 * 0.0178261) / 4 = **0.0050051665** (logged 0.0050051664).
  A row-190 seed would give 0.0050046380.
- Row 176 (-102.788 s) is the only row before -102.0 s with voltage 16.2549534. So the last reset ran
  at row 176. Rows 177 to 190 had no current and no reset, so their norm stayed at the initial value because
  nothing changed it. The 2 s window therefore ended between -102.788 s and -102.778 s, which puts the start
  of the "connected" timer about 0.25 s before the first logged row.

## Does the 100 Hz log hold every estimator call?

PLAN-fwmatch names unlogged samples as one way F1 could fail. Two readings say flight A's log holds every
call:

- 14,439 rows run from -104.528177 s to +39.851822 s: 14,438 intervals in 144.380 s, a mean of exactly
  10.000 ms.
- In the air, with current on every row, the stored signed R recovered from consecutive predictions
  (`pred[k+1] = V[k] + R (I[k] - I[k+1])` after a reject) agrees between rows 10452-10453 and 10453-10454
  (-0.02549 and -0.02547 Ohm per pack). Using it, row 10455's prediction comes out at 16.107675 (logged
  16.1076679). A hidden call with current between these rows would have moved the stored OCV and broken that
  chain.

The row-191 mismatch is explained by the window end, not by a missing sample.

## Expected effect on PLAN-fwmatch (stated before the run)

- The covariance does not depend on voltage, so seeding at row 190 instead of 176 changes no accept or reject
  decision and no norm. **F1, F2, F3 and T1/T2 are unaffected.**
- The seed's OCV is 1.82 mV too low. After the row-191 update this leaves about 0.25 mV of OCV error
  ((1 - 0.8633) * 1.82 mV) and a 5e-7 Ohm-per-cell error in R, so the predictions of the next row or two with
  current are off by about 0.25 mV. A rejected sample then resets OCV to `V + R I`. **F4 is a median over
  thousands of rows, so its outcome should not change.**
- A firmware-exact seed rule would be: the last row, before the first update whose prediction does not
  match its own voltage, at which a reset could have run. For flight A that is row 176, found by matching
  the row-191 prediction. This is a candidate for fwmatch 0.2.0, not a change to the pre-registered run.

## Limits

These are single rows read by hand from one flight. The "connected" timer's start is inferred, not logged.
The pilot's raw file is not published; only the few values above are quoted.

# NOTE 2026-11-15 (3): PX4's own offline replay tool does not use the firmware's estimator settings

Study: battery-r-2026 (px4-log-health). Status: **exploratory; source reading plus one hand-checked log row**.
It does not grade any plan and does not change any frozen file.

Text: CC BY 4.0. Code: Apache-2.0. Made with AI agents working in the name of MV2, Munim, Inc.
(mv2@munimtech.com). No human has checked these numbers.

## Summary

PX4 ships an offline replay script for its battery internal-resistance estimator,
`src/lib/battery/int_res_est_replay.py`. It was added on 2 July 2024 (7102968), on the same day as the
estimator itself (f65653a), and was last changed in
[fd8df2e](https://github.com/PX4/PX4-Autopilot/blob/fd8df2e84d04ac9fff36ece9eda1c5a0a432bc69/src/lib/battery/int_res_est_replay.py)
(#23351, July 2024). Its header says it "can lead to slightly different results than the online estimation"
because log rates differ from the online rate. Reading it next to
[battery.cpp](https://github.com/PX4/PX4-Autopilot/blob/6ea3539157ca358c70a515878b77077af7d4611d/src/lib/battery/battery.cpp)
and `battery.h` shows five more differences that have nothing to do with log rate:

| | Firmware (`battery.cpp` / `battery.h`) | Replay tool (defaults) |
| --- | --- | --- |
| Forgetting factor | `LAMBDA = 0.95` (constant) | `-l`, default **0.99** |
| Initial covariance | `diag(1.5 n, 0.1 n)` (`OCV_COVARIANCE`, `R_COVARIANCE`) | `np.diag([1.2 * n_cells, 0.1 * n_cells])` |
| Which samples update | only when `current_a > FLT_EPSILON` | every sample, including zero current |
| Tie in the norm test | rejected (`candidate < stored` required) | accepted (rejects only if `norm(P) < norm(P_temp)`) |
| OCV at the start | the measured voltage at each reset (first 2 s after connect) | `V[0] + 0.005 n I[0]`, never reset |

`LAMBDA`, `OCV_COVARIANCE` and the tool's defaults are the same today as when both files were added in July
2024, so the two have differed from the start. With `-l 0.95` the forgetting factor can be matched from the command line. The initial
covariance and the sample handling cannot.

## Checked against a logged firmware value

The shared FMUv6C log from forum topic
[49724](https://discuss.px4.io/t/battery-resistance-estimate-reaches-zero-and-persists-under-load-fmuv6c-bat1-r-internal-1/49724)
(4S, flight A, logged at 100 Hz from power-on) has a fresh estimator at its first sample with current:
`I = 0.00549374986 A`, `V = 16.2002678 V`. The firmware logged `estimation_covariance_norm = 0.960527122` and
`voltage_prediction = 16.2001572`. Before that row and right after it, the logged norm is the initial
6.01331854 (= sqrt(6^2 + 0.4^2), so 1.5 n with n = 4).

From a reset, with `P = diag(a, b)`, `x = [1, -I]` and `s = a + b I^2`, one update gives
`P'00 = a (lam + b I^2) / (lam (lam + s))`, `P'11 = b (lam + a) / (lam (lam + s))` and
`P'01 = a b I / (lam (lam + s))`:

- **Firmware settings** (a = 6, b = 0.4, lam = 0.95): P'00 = 0.8633188, P'11 = 0.4210519, P'01 = 0.0019970,
  norm **0.9605270** (0.96052697 before rounding). The logged value is 0.960527122, the same to 6 digits. The prediction `16.2002678 - 0.00549374986 * 0.02 =
  16.2001579` matches the logged 16.2001572 to within float32 spacing. This also shows that the reset in the 2 s
  window runs in the same call as the update.
- **Tool defaults** (a = 4.8, b = 0.4, lam = 0.99): P'00 = 0.8290239, P'11 = 0.4040396, P'01 = 0.0018402,
  norm **0.9222448**, 4% off at the very first update. The tool would also already have updated on the three
  zero-current samples before this one, which the firmware skipped.

So a replay made with the tool's defaults is not a replay of what the firmware did, even before any question of
log rate. This matters when a log is replayed to explain a collapsed estimate (as in topic 49724, see
NOTE-gate-2026-11-15.md), because how many updates the gate accepts depends on the forgetting factor and the
covariance.

## What is not known yet

- How much the differences change the outcome over a whole flight is not measured here. PLAN-fwmatch.md
  pre-registers that check: it replays flight A under firmware settings and under the tool's settings, compares
  each sample's accept/reject decision with the logged norm changes, and counts published zeros in the air.
  It also tests whether a 100 Hz log is enough to replay the estimator sample for sample.
- Whether the tool's defaults were chosen on purpose (for example for lower log rates) is not stated in the
  source or in the commits we read.

## Code

`fwmatch.py` (this folder) implements both settings and two mixed ones, so the effect of the constants can be
separated from the effect of the sample handling. `test_fwmatch.py` checks the hand-worked first update above,
the tie and low-current rules, and an exact self-match on a synthetic log.

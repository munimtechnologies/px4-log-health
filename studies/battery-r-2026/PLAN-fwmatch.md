# PLAN-fwmatch: does a sample-by-sample replay reproduce PX4's logged battery-resistance estimator?

Study: battery-r-2026 (px4-log-health). Pre-registered 2026-11-15, before any model has been run over the file.
Text: CC BY 4.0. Made with AI agents working in the name of MV2, Munim, Inc. (mv2@munimtech.com).

## What we already know (disclosed)

We have read the logged norms, currents and predictions of flight A by hand (NOTE-gate-2026-11-15.md: 3 accepted
updates between +2.27 s and +35.57 s; published value 0 in 3,685 airborne samples) and the first logged update
(NOTE-replay-tool-2026-11-15.md). No replay model has been run over the file.

## Data

`nov14-flight-a-100hz-battery.csv` (sha256 07ed7143e8ab7775e51e69851558b429fd3787fd351b86c305cedb31ea298a4c),
shared privately by its owner. Only derived numbers are published. 4S, logged at 100 Hz from power-on.

## Method (fwmatch.py 0.1.0, run_fwmatch.py)

- Seed: the last row whose logged norm equals the initial 6.01331854 (the last reset). Settings that start at
  `seed` begin there with the row's voltage; `replay_tool/first` starts at row 0, as the tool does.
- Decisions are compared on every row after the seed. Logged accept = the logged norm differs from the previous
  row's.
- Airborne = `landed_last_reported == 0`. Published zero = `max(R_pack / 4, 0) == 0`.
- Runs: firmware/seed, replay_tool/first, replay_tool/seed, tool_constants_fw_handling/seed,
  fw_constants_tool_handling/seed.

## Predictions

- **F1.** firmware/seed: accept/reject decisions agree with the log on at least 99% of rows.
- **F2.** firmware/seed: the number of accepted updates in the air equals the logged number exactly.
- **F3.** firmware/seed: published zeros in the air equal the logged count exactly.
- **F4.** firmware/seed: median |model prediction - logged voltage_prediction| on rows with current is below 1 mV.
- **T1.** replay_tool/first: decisions agree on fewer than 95% of rows.
- **T2.** replay_tool/first: fewer than 3,000 airborne rows publish 0 (low confidence; direction is a guess).

Mixed settings are exploratory, with no predictions.

## Reading the result

- F1-F4 hold: the model is a faithful replay at this log rate, and the in-tree tool's differences can be
  measured on real data.
- F1 fails: either the estimator runs on samples the 100 Hz log does not contain, or float32 ties flip
  decisions. The first disagreement time and the norm error will tell which. Report it as a limit of 100 Hz logs.
- All results are reported, misses included, in GRADE-fwmatch.md.

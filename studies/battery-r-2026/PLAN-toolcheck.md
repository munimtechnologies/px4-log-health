# battery-r-2026, tool check: does PX4's own replay tool compute what the firmware computes?

Pre-registered 2026-11-12 by MV2 of Munim, Inc. (mv2@munimtech.com). Written with AI agents working in MV2's name. Text CC BY 4.0, code Apache-2.0. PX4 code quoted in the job's outputs is PX4-Autopilot's, BSD-3-Clause.

Part of the px4-log-health study battery-r-2026. PX4's battery-resistance estimator (src/lib/battery/battery.cpp, Battery::updateInternalResistanceEstimation, added in PX4 PR #23205, 2024) ships with an offline replay tool next to it, src/lib/battery/int_res_est_replay.py, which PR #23205 says was run on flight logs while the estimator was developed. The earlier steps of this study found the firmware's published estimate stuck at 0 in about 9-14% of measurable public flights (GRADE.md, GRADE-onset.md), and used MV2's own float32 port of the firmware update (replay.py 0.1.0) for the replay, simulation and cost steps.

Reading the tool's source on GitHub (2026-11-12, before any code was run) suggests that it does not run the firmware's settings: its forgetting factor defaults to 0.99 (the firmware's LAMBDA is 0.95), its prior covariance on the open-circuit voltage is 1.2 per cell (firmware 1.5), it starts from the first sample rather than resetting for 2 s, it updates on every sample (the firmware only when current > FLT_EPSILON), and it shows max(R per cell, 0.001), so a user of the tool cannot see the value 0 that the firmware publishes. This step checks those differences from the exact source lines, checks MV2's port against PX4's own update function, and measures what the forgetting factor changes, on simulated data. It reads no flight log.

## Method (fixed now)

- **Sources.** The job downloads int_res_est_replay.py, battery.h and battery.cpp from PX4-Autopilot's main on the day it runs, records their sha256 and the last commit touching each, and saves the lines it relies on (px4-tool-lines.txt). It downloads replay.py (git blob 4199dd45a5e7cae90ed81fc157cf2cc297511936) and sim.py 0.1.0 (git blob 74ecc4bde47592cd04e4fe8b929a787edced36ac) from px4-log-health's main and checks both hashes.
- **PX4's update function.** toolcheck.py takes the tool's `rls_update` function out of the downloaded file with Python's ast module and runs only that function (the rest of the tool, its plotting and log reading, is not run).
- **Data.** One cell of the simulation model of PLAN-sim.md (sim.py 0.1.0, unchanged): typical sensor noise (15 mV, 0.05 A), no ground events, no current delay; 100 runs from its own seed ([2026, 11, 12, 7], not one of the simulation step's seeds). 60 s on the ground at 0.8 A, 3 s spool-up at 3 A, 120 s of flight at about 15 A, at 100 Hz.
- **Five ways to run the estimator on the same samples:**
  - **fw32:** MV2's port of the firmware (sim.run_estimator, float32), the firmware's settings and call pattern.
  - **fw64:** the same port in float64 (replay.replay with dtype float64).
  - **T1:** PX4's `rls_update` (float64) with the firmware's settings and call pattern: forgetting factor 0.95, state reset to (V, 5 mOhm per cell) and covariance diag(1.5, 0.1) per cell during the first 2 s, updated only when current > FLT_EPSILON, published value max(R per cell, 0) changed only when an update is accepted.
  - **T0:** the tool as shipped: its own loop (initial state from the first sample, covariance diag(1.2, 0.1) per cell, every sample updated, no reset), forgetting factor 0.99 (its default), shown value max(R per cell, 0.001).
  - **T0-l95:** the same as T0 with `-l 0.95`, the forgetting factor a user who reads battery.h would pass.
- **Per run:** at takeoff = the value (published, or R per cell clamped at 0 for T0 and T0-l95) is 0 at the last sample before takeoff; collapsed in flight = 0 for the whole second half of the flight (PLAN-sim.md's rules); R at the end; the share of airborne samples with an accepted update. For T0 and T0-l95 also: the samples where R per cell is at or below 0 and what the tool shows there.

## Predictions (fixed now)

- **C1, the settings differ:** the downloaded sources contain all six of: the tool's `-l` default 0.99; the tool's initial covariance `np.diag([1.2 * n_cells, 0.1 * n_cells])`; the tool's shown value `max(R_est[index]/n_cells, 0.001)`; battery.h's `LAMBDA = 0.95f`; battery.h's `OCV_COVARIANCE = 1.5f`; battery.cpp's `math::max(_RLS_est(1) / _params.n_cells, 0.f)`. Each is checked by a fixed pattern on the downloaded text; C1 holds if all six are found. Two more facts are reported, not graded: the tool has no condition on current before updating, and battery.cpp updates only `if (current_a > FLT_EPSILON)`.
- **P1, MV2's port matches PX4's own update:** T1 and fw64 make the same accept/reject decision on every update and their published values differ by at most 1e-9 Ohm per cell at every sample, in at least 99 of the 100 runs.
- **P2, the default forgetting factor lets more updates through:** T0's share of airborne samples with an accepted update is higher than fw32's in at least 90 of the 100 runs.

A prediction that cannot be graded is reported as not graded, never as held.

## Reported, not graded

Per way of running: runs at 0 at takeoff, runs collapsed in flight, the median and range of R at the end and of |R_end / 2.7 mOhm - 1|, and the airborne accept share. For T0 and T0-l95: runs with R per cell at or below 0 at any airborne sample, and the smallest value shown there. fw32 against fw64: runs with different accept decisions and the largest difference in the published value (float32 against float64).

## Fixed interpretation

- **C1 holds:** PX4's shipped replay tool, run with its defaults, is not the firmware's estimator: it forgets old data more slowly, starts differently and can never show 0. Plots made with it can look healthier than what the firmware publishes. Any difference whose line is not found is dropped from every claim.
- **P1 holds:** PX4's own update function, given the firmware's settings, reproduces MV2's port exactly in float64, which independently checks the port behind the replay, simulation and cost steps. **P1 fails:** the port or this step's wrapper differs from PX4's own code; the first difference is located and reported, and the replay, simulation and cost results carry that caveat until it is explained.
- **P2 holds:** with its default, the tool accepts more updates in flight than the firmware does, so it overstates how much the in-flight estimate moves. **P2 fails:** published as measured.
- If C1 and P1 hold, MV2 may offer PX4 a small change to int_res_est_replay.py (default forgetting factor and OCV prior from battery.h, the firmware's 2 s reset and current condition, and showing 0 the way the firmware publishes it, or labelling the floor), as one GitHub issue or pull request, only if no open issue or pull request covers it, with these results and their limits.

## Limits, stated before running

- One simulated cell of one battery model; the numbers describe these settings on this model, not real vehicles. The tool is meant for logged samples (about 5 Hz); here it gets the 100 Hz samples the firmware sees.
- The tool's plots and log reading are not run, only its update function and the loop and constants quoted from its source, so a difference in how the tool reads logs would not be seen.
- A different forgetting factor is not a bug by itself; the tool may have been tuned on purpose. This step shows the difference and its effect, not which setting is right.

## Files

toolcheck.py 0.1.0 and test_toolcheck.py (synthetic tests, run before anything else, that check the wrapper against a hand-written copy of the update and the shown-value floor), job_fetch_toolcheck.py (downloads and hashes, px4-tool-lines.txt). toolcheck.py runs only after it has checked that this plan on main is byte-identical to its own copy (line endings normalised). Outputs: toolcheck-summary.json and toolcheck-runs.json.

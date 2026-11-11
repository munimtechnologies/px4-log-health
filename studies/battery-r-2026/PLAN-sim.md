# battery-r-2026, simulation: can sensor noise on the ground collapse PX4's resistance estimate before takeoff?

Pre-registered 2026-11-11 by MV2 of Munim, Inc. (mv2@munimtech.com). Written with AI agents working in MV2's name. Text CC BY 4.0, code Apache-2.0.

Part of the px4-log-health study battery-r-2026. Earlier steps found that PX4's online battery internal-resistance estimate is stuck at 0 in about 9-14% of measurable public flights, mostly from before takeoff (GRADE.md, GRADE-onset.md). The replay step (GRADE-replay.md) ran PX4's own update, ported line for line in float32 (replay.py 0.1.0), on the logged ~5 Hz voltage and current, started fresh at the start of each log. It tracked the real estimate where it works but rebuilt none of the 11 collapses on steep data. Its main limit: default logs start at arming, while PX4 has been updating at its sample rate (100 Hz on the analog path) since boot, and on 43% of flights the estimator had already converged before the log began.

This step looks where the logs cannot: a simulated flight from boot, at 100 Hz, through the same port. It asks one question: with plausible voltage and current sensor noise on the ground, can PX4's gated update drive the estimate to 0 before takeoff and keep it there? It is a simulation of a model battery, not a measurement of real vehicles. Its results say what the estimator can do, not how often it does it in the fleet.

## Why it might (reasoning written before any simulation was run)

- PX4 starts the estimate at R = 5 mOhm per cell with a prior covariance of 0.1 per cell on R, a standard deviation of about 316 mOhm per cell, about 100 times the typical value (census median 2.7 mOhm per cell).
- At a constant idle current, the regressor [1, -I] barely changes, so the data say almost nothing about R. A plain least squares would let R drift with the noise; PX4's gate accepts an update only if the covariance norm falls, so after a short start most idle updates are rejected.
- A small current change on the ground (an ESC tone, a radio burst, noise around a quantisation step) can then be accepted with a large gain on R, so one noisy voltage sample could move R a long way, possibly below 0. The gate would then hold that value, and in flight the norm is already low enough that few updates are accepted (GRADE-replay.md: about 1.5% of airborne intervals). That would match what the fleet shows: collapse before takeoff, persisting.

## The model (fixed now)

- **Estimator:** `replay.Rls` and `replay.replay` from replay.py 0.1.0 on px4-log-health's main (git blob 4199dd45a5e7cae90ed81fc157cf2cc297511936), unchanged. sim.py runs the same arithmetic vectorised over seeds; a test requires it to equal `replay.replay` exactly (every published value, raw value and norm, float32) before anything else runs. PX4 constants come from replay.py (LAMBDA 0.95, R 5 mOhm and OCV 4.2 V per cell at reset, covariances 1.5 and 0.1 per cell, reset for the first 2 s after connection). The job also saves the matching lines of PX4's src/lib/battery/battery.cpp and battery.h from main on the day it runs (px4-estimator-lines.txt); differences from replay.py are reported first.
- **Battery:** 4S, true resistance R0 = 2.7 mOhm per cell, one RC pair (R1 = 1.0 mOhm per cell, tau 20 s), open-circuit voltage 4.10 V per cell falling linearly by 0.60 V per cell over a 5 Ah capacity. V = 4 x (OCV - R0 x I - V_RC).
- **Timeline at 100 Hz:** connection at t = 0; ground at 0.8 A for 60 s; arming with motors spooling at 3 A for 3 s; takeoff; 120 s of flight at 15 A + 4 A x sin(2 pi 0.2 Hz t + random phase) + load noise (AR(1), 1 A standard deviation, correlation 0.95 per sample).
- **Ground events (factor):** none, or 5 bursts of +1.5 A for 0.3 s each at random times between 5 and 55 s (ESC tones, servos, radios).
- **Sensors:** measured voltage = true + Gaussian noise, rounded to 14.5 mV; measured current = true + Gaussian noise, rounded to 29 mA (an illustrative 12-bit ADC behind an analog power module). Noise levels (factor): quiet (5 mV, 0.02 A), typical (15 mV, 0.05 A), noisy (40 mV, 0.15 A).
- **Timing (factor):** current sampled at the same time as voltage, or 2 samples (20 ms) late.
- **Variants:** px4 (the port as is); f1 (the same, with the prior covariance on R 100 times smaller, 0.001 per cell); f2 (the same code, but the battery counts as connected only at arming, so the estimator starts there, like the replay of a log that starts at arming). f1 and f2 are candidate changes, not PX4 behaviour.
- 3 noise levels x 2 event settings x 2 timings = 12 cells, 200 seeded runs each. All three variants see the same simulated data in a cell.

## Outcomes per run

- **Collapsed at takeoff:** the published estimate is 0 at the last sample before takeoff.
- **Collapsed in flight (the census rule):** the published estimate is 0 for the whole second half of the flight.
- **Relative error at the end:** |R_end / R0 - 1|, with R_end the published estimate at the last sample.
- Reported, not graded: the share of runs that touched 0 at any time on the ground, the time of the first 0, the norm at takeoff, the share of airborne samples with an accepted update, and every figure per cell.

## Predictions (fixed now)

px4 pooled = all 12 cells of the px4 variant (2,400 runs).

- **H1, clean data works:** px4, quiet, no events, no delay: collapsed at takeoff in at most 5% of runs.
- **H2, noise matters:** px4, pooled over events and timing: the share collapsed at takeoff in the noisy cells is at least 10 percentage points above the share in the quiet cells.
- **H3, it persists:** px4 pooled, of the runs collapsed at takeoff, at least 70% are also collapsed in flight. Graded if at least 30 runs collapsed at takeoff.
- **H4, a small prior prevents it:** f1 pooled, the share collapsed at takeoff is at most half of px4 pooled's. Graded if px4 pooled is at least 5%.
- **H5, starting at arming prevents it:** f2 pooled, the share collapsed in flight is at most half of px4 pooled's. Graded if px4 pooled's share collapsed in flight is at least 5%.
- **H6, the model is fair where the estimator works:** px4 pooled, runs not collapsed in flight: median relative error at the end at most 0.5. Graded if at least 100 such runs.

A prediction that cannot be graded is reported as not graded, never as held.

## Fixed interpretation

- **H6 fails:** the simulated estimator is off even when it does not collapse, so the model is not a fair stand-in. H2-H5 are then reported as descriptions only.
- **H1 fails:** PX4's update, started fresh from boot, collapses on clean simulated data. Before anything is said in public, the port is checked against px4-estimator-lines.txt; if it matches, that is the strongest result of the study.
- **H1, H2 and H3 hold:** noisy voltage or current sensing on the ground, together with the wide prior on R and the gate, is enough to set the estimate to 0 before takeoff and keep it there through the flight. The px4-log-health battery note may then name sensor noise at idle as one mechanism (from a simulation), next to the wrong current scale or offset it already names. If H4 or H5 also holds, that change is a candidate fix; it would be offered to PX4 as one GitHub issue with the simulation code and numbers, only if no open issue covers it, and with the limits below.
- **H2 fails:** in this model, sensor noise is not what collapses the estimate. That is published as a negative result, and the collapses stay unexplained by noise.
- **H3 fails (graded):** the simulated collapses recover in flight, unlike the fleet's, so this model misses what holds them at 0.
- Whatever the grade, the share collapsed in flight per px4 cell is compared, as a description only, with the fleet's 10 of 75 (GRADE.md).

## Limits, stated before running

- One battery model, one flight profile and illustrative noise levels; real power modules, ADCs and filters differ. The fleet's own sensor noise at 100 Hz is not known from 5 Hz logs.
- The port's call pattern (reset during the first 2 s, update whenever current is above FLT_EPSILON) is replay.py's, which matched the logged estimate where it works (rho 0.71) but has not been reviewed by PX4 developers.
- A simulation can show a mechanism is possible and what changes remove it in the model. It cannot show that this mechanism is the one at work on a given vehicle; a from-boot, full-rate log of a vehicle that collapses can.

## Files

sim.py 0.1.0 and test_sim.py (synthetic tests, including exact equality with replay.replay, run before the simulation), job_fetch_sim.py (fetches replay.py 0.1.0 from main and checks its git blob hash, records sha256 hashes in job-fetched-sim.json, saves px4-estimator-lines.txt). sim.py runs only after it has checked that this plan on main is byte-identical to its own copy (line endings normalised). Outputs: sim-summary.json (predictions and pooled figures) and sim-cells.json (every cell).

## Appendix: a hand check of the outcome rules

Takeoff at index 4 of 10 samples. Published values [5, 5, 5, 0, 0, 0, 0, 0, 0, 0]: collapsed at takeoff (index 3 is 0) and in flight (indices 4-9; second half 7-9 all 0). [5, 5, 5, 2, 0, 0, 0, 3, 0, 0]: not at takeoff; second half [3, 0, 0], not in flight. [5, 5, 5, 0, 1, 1, 1, 1, 1, 1]: at takeoff, not in flight.

# battery-r-2026, tool check: a correction written before any result

Written 2026-11-12 by MV2 of Munim, Inc. (mv2@munimtech.com), with AI agents working in MV2's name. Text CC BY 4.0.

This note belongs to the pre-registered tool check, [PLAN-toolcheck.md](PLAN-toolcheck.md) (commit 4a99c34, 2026-11-12 02:22 UTC). It was written while that job was still queued, before it had run. No output of the tool check existed when it was written. PLAN-toolcheck.md is left unchanged, because the job checks that it is byte-identical. Its methods and graded predictions (C1, P1, P2) stand exactly as written. This note corrects one sentence of the plan's reading of PX4's tool, and with it one line of the fixed interpretation and one item of the possible upstream change.

## What the plan got wrong

The plan says the tool "shows max(R per cell, 0.001), so a user of the tool cannot see the value 0 that the firmware publishes". The fixed interpretation of C1 then says the tool "can never show 0". That is too broad. A second reading of `src/lib/battery/int_res_est_replay.py` on 2026-11-12 found the following:

- The floored value goes into the variable `internal_resistance_stable`. The tool uses it only to compensate voltage for load: in the open-circuit-voltage line of the voltage plot, in the smoothed OCV, and in the two "SoC with estimator" curves.
- The resistance plot ("Internal resistance estimate (per cell) [mOhm]") draws the **unfloored** estimate, `R_est * 1000 / n_cells`. The mean the tool prints is also unfloored (`np.mean(R_est) / n_cells`).

So a user of the tool **can** see a resistance of zero or below on its resistance plot. Where the firmware publishes max(R per cell, 0), the plot shows the raw value, which can be negative. The 0.001 Ohm floor (1 mOhm per cell) affects only the tool's load-compensated voltage and state-of-charge curves.

## Corrected interpretation, fixed now

- **C1 is graded exactly as the plan says**: the same six patterns, all of which must be found. Only what C1 means changes.
- **If C1 holds**, the claim becomes this. PX4's shipped replay tool, run with its defaults, is not the firmware's estimator: it forgets old data more slowly, starts differently and updates on every sample. Where the firmware's estimate has collapsed to 0, the tool's resistance plot shows the raw value (which can be below 0), and its state-of-charge curves still apply at least 1 mOhm per cell of load compensation, while the firmware applies none. The tool's state-of-charge curves can therefore look healthier than what the firmware computes. Its resistance plot can look *worse* (negative). The plan's words "can never show 0" are withdrawn.
- **The reported value** `smallest_shown_where_at_or_below_0_mohm` (T0 and T0-l95) describes the value that feeds the tool's OCV and state-of-charge curves, not the resistance plot. It is reported under that meaning.
- **The possible upstream change** in the plan's last interpretation item, "showing 0 the way the firmware publishes it, or labelling the floor", becomes: in the state-of-charge curves, use the firmware's floor of 0 (or label the 0.001 floor), and optionally draw the firmware's published value, max(R per cell, 0), next to the raw estimate on the resistance plot. The other items (default forgetting factor and OCV prior from battery.h, the firmware's 2 s reset and its current condition) are unchanged.

## Two further observations, reported and not graded

- The tool's header describes a `-d` option for filtering the measurements, but no `add_argument` defines it. The tool reads `battery_status.voltage_v` and `battery_status.current_a` without filtering them.
- The job's saved source lines (px4-tool-lines.txt) were chosen before this correction. They include the line that assigns `internal_resistance_stable[index]`, but not the plotting and print lines quoted above. Before any of this goes upstream, those lines will be printed by a job from the same git blob of the tool that the tool-check job records. Until then, the quotes in this note come from reading the source on GitHub, and they are marked as unconfirmed.

## Why this note exists

A pre-registration that misreads the code it tests should be corrected in the open, with a date, before the results are known, not quietly after. The graded predictions are unaffected. Only the words used to explain them change, and the change makes the eventual claim narrower.

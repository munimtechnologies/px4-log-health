# battery-r-2026, trace: a timing readout added before any log (note to PLAN-trace.md)

Written 2026-11-12 by MV2 of Munim, Inc. (mv2@munimtech.com), before any qualifying from-boot, full-rate log has been seen. Written with AI agents working in MV2's name. Text CC BY 4.0. PLAN-trace.md itself is unchanged; its gates, predictions T1-T3 and fixed interpretation stand as written.

## Why

The simulation step (GRADE-sim.md) found that the collapses that persist into flight are driven by a timing offset between the voltage and current samples, not by noise at idle. Noise alone drove the estimate to 0 on the ground, but it recovered at takeoff. PLAN-skew.md tests the explanation: at a load step, if the voltage and current changes land in different samples, PX4's update sees "the current changed, the voltage did not" and the resistance goes to about 0.

PLAN-trace.md's attribution would not name this case. At such a step the voltage barely moves, so the update is not "wrong-way" (dV x dI > 0), and it would fall under mechanism C. This note adds a readout for it, and one competing prediction, before any log arrives.

## Added readouts (battr_trace.py 0.1.1 implements them before any log is read; nothing else changes)

- **Flat step:** an accepted step update in the descent window (PLAN-trace.md, section 3) is flat if |dV| < 0.25 x n_cells x max(R_before, 1 mOhm) x |dI|. R_before is the replayed raw R per cell just before that update. In words: the voltage moved less than a quarter of what the current estimate expects for that current change.
- **Step offsets:** for every pair of consecutive samples on the ground, or in the first 5 s after takeoff, where the current changes by at least 1 A, find within +-5 samples the sample with the largest current change and the sample with the largest voltage change of the opposite sign. The offset is (current sample - voltage sample), in samples, so positive means the current is late. Each step is reported with its time, size and offset.

## Added predictions (fixed now; graded only if G2 and G3 hold)

- **T4, a timing collapse (competing with T2):** flat and wrong-way step updates together make at least 50% of the decrease in R over the descent window.
- **T5, the step is misaligned:** if a step with |dI| >= 1 A falls within the 3 samples before the logged onset, its offset is not 0. Not graded if no such step exists.

## Fixed interpretation of the additions

- **T4 and T5 hold:** on this vehicle the estimate collapsed at a load step where the voltage and current changes landed in different samples, the mechanism PLAN-skew.md tests. If PLAN-skew.md's K1-K3 also hold, this is the basis for one PX4 issue. It would contain the derived numbers only, the log's owner would be asked first, and only if no open issue covers it.
- **T4 holds, T5 fails or is not graded:** the collapse came from steps the voltage did not follow, but not at an offset visible at this sample rate. A filter or a sensor fault is then as likely as a timing offset.
- **T4 fails:** timing is not what collapsed this vehicle's estimate; T2's reading stands.
- One log is one case.

## Limit

Voltage and current in one battery_status message come from the same driver sample. An offset seen in the log therefore comes from inside the driver or the power module (sampling order, conversion or filtering), which is exactly what the estimator sees.

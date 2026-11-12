# battery-r-2026, timing offset: is one sample between voltage and current enough to collapse PX4's resistance estimate?

Pre-registered 2026-11-12 by MV2 of Munim, Inc. (mv2@munimtech.com), before this simulation was run. Written with AI agents working in MV2's name. Text CC BY 4.0, code Apache-2.0.

Part of the px4-log-health study battery-r-2026. The simulation step (PLAN-sim.md, graded in GRADE-sim.md) ran PX4's own resistance update from boot at 100 Hz on a model battery. Sensor noise on the ground often drove the estimate to 0, but those collapses almost all recovered at takeoff: 8 of 1,200 runs were at 0 in flight with current and voltage sampled together. With the current sample 20 ms (2 samples) late, 518 of 1,200 were. The delayed runs fell at the first load step, the motors spooling up. That was one of two pre-registered timing levels. The explanation below was worked out after those results and is tested here.

## The hypothesis (written before this simulation)

PX4's Battery::updateInternalResistanceEstimation() fits V = OCV - R x I by recursive least squares with regressor x = [1, -I]. It accepts an update only if the covariance norm falls, and that decision depends on x alone, never on the voltage. After a rejected update it sets OCV = V + R x I.

- **Current late (offset > 0):** at a load step, the voltage sample drops while the current sample has not moved yet. x is unchanged, so the gate treats it like any idle sample and usually rejects it, and the reject branch moves OCV down by the whole voltage drop. When the current sample then rises, x carries new information and the update is accepted with a large gain. But the voltage no longer changes, so the data now say R is about 0.
- **Voltage late (offset < 0):** the current sample rises first, with the voltage unchanged. That update is accepted at once and says R is about 0. The voltage drop arrives with x unchanged and is rejected.
- **Either way,** after such an update the covariance is small and later updates are mostly rejected (GRADE-sim.md: about 3 accepted updates in 12,000 airborne samples), so the estimate keeps the value.

It predicts that one sample of offset in either direction is enough. It also predicts that removing the reject branch's OCV update prevents the collapse when the current is late but not when the voltage is late. And a load step that ramps over many samples should weaken it, because then most samples see the voltage and current change together.

## The model (fixed now)

- **sim.py 0.1.0** (git blob 74ecc4bde47592cd04e4fe8b929a787edced36ac) and **replay.py 0.1.0** (git blob 4199dd45a5e7cae90ed81fc157cf2cc297511936), both unchanged and checked by hash before anything runs. They provide the same model battery, timeline (60 s ground at 0.8 A, 3 s spool at 3 A, 120 s flight around 15 A), sensor quantisation, outcome rules and vectorised port of PX4's update (equal to replay.replay() exactly; a test checks it again here). No ground events.
- **Offset (factor):** -2, -1, 0, +1, +2 samples at 100 Hz. +d: the current sample is d samples late. -d: the voltage sample is d samples late.
- **Noise (factor):** none, quiet (5 mV, 0.02 A), typical (15 mV, 0.05 A), noisy (40 mV, 0.15 A), Gaussian, then quantised to 14.5 mV and 29 mA as in sim.py.
- **Rise (factor):** the arming step (0.8 to 3 A) and the takeoff step (3 A to the flight current) either jump in one sample, as in sim.py, or ramp linearly over 25 samples (0.25 s).
- **Variants:** px4 (the port as is), and keep (the same, except that a rejected update leaves the OCV estimate unchanged). keep is a diagnostic for the hypothesis, not a proposed change: without that line, the OCV estimate goes stale as the battery discharges.
- 4 noise levels x 2 rises, 200 seeded runs each. Within a (noise, rise) pair, every offset and both variants see the same simulated current, voltage and noise draws, so offsets are compared on the same flights.

## Outcomes per run

- **Low at the end:** the published estimate at the last sample is below 0.5 x R0 (1.35 mOhm per cell; R0 = 2.7).
- Reported, not graded: collapsed in flight (sim.py's census rule), collapsed at takeoff, the published estimate just before the spool-up and 1 s after it starts, and the share of airborne samples with an accepted update.

## Predictions (fixed now)

Pooled means over the 4 noise levels (800 runs), jump steps unless stated.

- **K1, aligned is fine:** px4, offset 0: low at the end in at most 10% of runs.
- **K2, current one sample late is enough:** px4, offset +1: low at the end in at least 50%.
- **K3, voltage one sample late is enough:** px4, offset -1: low at the end in at least 50%.
- **K4, the reject branch carries it when current is late:** offsets +1 and +2 pooled (1,600 runs): keep's share low is at most half of px4's. Graded if px4's share is at least 20%.
- **K5, but not when voltage is late:** offsets -1 and -2 pooled: keep's share low is at least half of px4's. Graded if px4's share is at least 20%.
- **K6, the mechanism without noise:** noise none, px4: the median published estimate at the end is below 0.5 x R0 at offset +1 and at offset -1, and between 0.5 and 1.5 x R0 at offset 0.
- **K7, ramps weaken it:** px4, offsets -1 and +1 pooled (1,600 runs): the share low with 0.25 s ramps is at most half the share with jumps. Graded if the share with jumps is at least 20%.

The offset 0 and +2 cells with quiet, typical and noisy noise repeat sim.py's no-event cells with new seeds, so K1 is partly known in advance: at offset 0, 4 of those 600 runs collapsed in flight, and the noisy cells ended lower than the others. The new information is offsets of one sample, offsets where the voltage is late, the noise-free cells, the keep variant and the ramps. A prediction that cannot be graded is reported as not graded, never as held.

## Fixed interpretation

- **K6 fails at offset 0:** the port does not recover R0 from clean, aligned data in this model. Nothing else is interpreted until that is explained.
- **K1, K2 and K3 hold:** in this model, PX4's estimate goes from about right to about 0 when voltage and current at a load step are sampled one sample apart, in either order. The px4-log-health battery note may then say that, in simulation, a one-sample offset between the voltage and current samples at a load step is enough to collapse the estimate. It must be labelled as a simulation, and it must say that whether real power modules have such an offset is not known.
- **K2 or K3 fails:** one sample is not enough in that direction, and the note says only what held.
- **K4 and K5 both hold:** the order explanation above is supported. If either fails, the explanation is incomplete, and the report says which part.
- **K7 holds:** the effect depends on how sharply the load changes, so slow motor ramps protect. **K7 fails (graded):** even 0.25 s ramps do not.
- **Nothing is proposed to PX4 from this step alone.** A PX4 issue needs a real vehicle that shows it: a from-boot, full-rate log where the collapse happens at a load step with the voltage and current changes in different samples (PLAN-trace.md with NOTE-trace-align.md). A candidate fix would need its own plan.

## Limits, stated before running

- One model battery and flight profile. The offsets are whole samples. A real offset of a fraction of a sample splits some load steps across two samples and leaves others aligned, which this does not model.
- Real load steps are neither jumps nor clean linear ramps. Real power modules may filter voltage and current differently, which acts like a frequency-dependent offset. The job saves the PX4 lines that show how the analog and INA226 drivers read voltage and current, for reading only.
- keep changes the estimator in a way that would cause other problems on a real battery. It is used only to test the explanation.

## Files

skew.py 0.1.0 and test_skew.py (synthetic tests, including exact equality with sim.run_estimator and replay.replay, run before the simulation), job_fetch_skew.py (fetches sim.py and replay.py from main, checks their hashes, records sha256 hashes in job-fetched-skew.json, and saves px4-skew-lines.txt). skew.py runs only after it has checked that this plan on main is byte-identical to its own copy (line endings normalised). Outputs: skew-summary.json (predictions and pooled figures) and skew-cells.json (every cell).

## Appendix: a hand check of the hypothesis, current one sample late

4 cells, OCV 16.4 V, true R 2.5 mOhm per cell (0.01 Ohm per pack), estimate already at 0.01 Ohm per pack. Before the step: I = 1 A, V = 16.39 V, OCV estimate 16.40. Step to 11 A at sample k: V[k] = 16.29 V, but the current sample still reads 1 A. x is unchanged, so the update is usually rejected, and OCV becomes 16.29 + 0.01 x 1 = 16.30 V. At sample k+1 the current reads 11 A and V = 16.29 V. The prediction is 16.30 - 0.01 x 11 = 16.19 V, an error of +0.10 V. An accepted update with a large gain on R moves the pack estimate toward (16.30 - 16.29) / (11 - 1) = 0.001 Ohm, i.e. 0.25 mOhm per cell, a tenth of the true value. If the OCV estimate had stayed at 16.40 (keep), the same update would point at (16.40 - 16.29) / 11 = 0.01 Ohm, the true value.

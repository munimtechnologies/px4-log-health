# battery-r-2026, trace: how battr_trace.py 0.1.1 reads NOTE-trace-align.md (note, before any log)

Written 2026-11-12 by MV2 of Munim, Inc. (mv2@munimtech.com), before any qualifying from-boot, full-rate log has been seen. Written with AI agents working in MV2's name. Text CC BY 4.0. PLAN-trace.md and NOTE-trace-align.md are unchanged. This note records how battr_trace.py 0.1.1 implements the parts of NOTE-trace-align.md whose wording leaves a choice. Each choice was made before any log was read.

## Flat step

- A step update is flat if |dV| < 0.25 x n_cells x max(R_before, 1 mOhm) x |dI|. dV and dI are the changes from the previous sample into the update's sample. R_before is the replayed raw R per cell just before that update.
- Quiet updates are never flat; only step updates can be.
- An update can be both flat and wrong-way (a small voltage change in the same direction as the current). For T4 it counts once: T4 uses the share of the decrease made by updates that are flat, wrong-way or both.
- The mechanism letter (A, B or C) of PLAN-trace.md is computed exactly as in 0.1.0 and ignores flat.

## Step offsets

- A step is a pair of samples (k-1, k) that are consecutive in time (gap at most 1.5 nominal intervals), both connected, with a current change of at least 1 A. The readout covers every sample before takeoff plus the first 5 s after it. If the log has no takeoff time, it covers the whole log, and the report says so.
- Inside the +-5 sample window, only consecutive, connected pairs count. The current sample is the largest change in the step's own direction, and the voltage sample is the largest change of the opposite sign. Ties go to the earlier sample.
- If no voltage change of the opposite sign lies in the window, the offset is reported as "none", not as 0.
- Every qualifying pair is reported, so a step spread over two samples shows up twice.
- Sign: offset = current sample - voltage sample, so positive means the current is late. PLAN-trace.md's lag readout runs the other way: a positive lag there means the voltage is late. Both are reported as defined.

## T5

- "Within the 3 samples before the logged onset" is read as the onset sample itself plus the 3 samples before it. The step into the onset sample is the update that reaches 0, so it must count.
- If more than one step of at least 1 A falls there, the largest is used (the earliest on ties).
- T5 is not graded if no such step exists. It is also not graded if the step has no voltage change of the opposite sign within +-5 samples: then no offset can be read, and "the voltage did not follow" is already what T4 measures. As NOTE-trace-align.md fixes, T4 and T5 are graded only if G2 and G3 hold.

## Also added (report only, no prediction)

The report now also lists the parameters BAT1_SOURCE and BAT1_I_OVERWRITE. BAT1_I_OVERWRITE replaces the disarmed current with a constant (battery.cpp), which would make arming look like a current step with no voltage change. The report also gives the full table of descent updates, as PLAN-trace.md's limits section promised.

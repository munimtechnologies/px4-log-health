# compare.py 0.2.0: changes after review

compare.py compares two PX4 flight logs of one vehicle (before and after a change). Its rules were pre-registered in [PLAN.md](PLAN.md), and the compare-2026 study is measuring their false-alarm rate on public pairs of flights where nothing changed. That study runs compare.py **0.1.0** exactly as frozen. Nothing below changes it.

On 2026-11-11, yigitcan-ozturk reviewed compare.py and this plan at commit 779220f ([discuss.px4.io topic 49705, post 13](https://discuss.px4.io/t/49705/13)). They made two suggestions for a later version and added one caution. All three are taken up in 0.2.0:

| # | Review point | What 0.1.0 does | What 0.2.0 does |
|---|---|---|---|
| 1 | Missing standard errors in the GPS lag (C1) and innovation (C2) comparisons appear to default to zero | Confirmed: `(se_ms or 0.0)`. A missing SE silently drops the "> 2 combined SE" condition, which leaves only the 30 ms one | An SE that is missing, 0, negative or not finite counts as unavailable. A change within 30 ms is still "same", because the rule needs both conditions to fire. A larger change with no usable SE on either log is "not comparable", and the row names which log lacked it |
| 2 | The verdict can read "no change beyond the rules" while several checks were not comparable | Confirmed: the verdict ignores coverage (the count was only in the JSON) | The verdict carries the count whenever data is missing, e.g. "no change beyond the rules (only 7 of 10 checks comparable)". The report's headline lists the checks that were not comparable, and the JSON has `coverage` {compared, checks, not_comparable, complete}. `--min-compared N` exits 2 when fewer than N of the 10 vehicle checks were compared |
| 3 | "Same observable configuration" is not "nothing physically changed" (payload, battery condition, flight profile) | The module docstring says so, but the report does not | Every report ends with a line stating this |

**Not changed:** every threshold (C1 to C8, E1, E2), parameter classes, firmware and parameter diffs, environment items. On logs where both standard errors are present, every item gets the same result as in 0.1.0.

**Tests:** test_compare.py 0.2.0 keeps all 0.1.0 cases and adds the following, all on synthetic rows:
- missing SE on a large change gives "not comparable", with the change still shown
- a 0, negative, NaN or text SE counts as missing
- missing SE on a change within 30 ms gives "same"
- the same three cases for C2
- coverage counts and verdict text with 2 or 5 checks missing, and with a "worse" item plus missing data
- nothing comparable gives "not comparable"
- the report's coverage line and physical caveat
- exit codes for `--fail-on-worse` and `--min-compared`

**When it ships:** the study job checks that compare.py and test_compare.py on main are byte-identical to its frozen copies before it reads any log. So 0.2.0 replaces them on main only after that check has passed, together with a px4-log-health release.

**How its effect will be measured:** after the study is graded, 0.2.0 is re-run on the same stored per-log figures (pairs-rows.jsonl). The re-run reports, per item, how many null and upgrade pairs move from a result to "not comparable", and how many verdicts gain a coverage note. This is reported next to the 0.1.0 grade, never in place of it.

MV2, Munim, Inc. (mv2@munimtech.com). Text CC BY 4.0; code Apache-2.0. Made with AI agents working in MV2's name.

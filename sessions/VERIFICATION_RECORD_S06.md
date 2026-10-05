# VERIFICATION_RECORD — Session 6

**Session:** Session 6 — Evaluation, Reporting & Demo
**Date:** 2026-10-05
**Engineer:** 

*Each task entry is created before the task starts. Challenge Agent findings are
dispositioned by CC under the engineer's standing instruction (2026-10-04) — see
`sessions/SESSION_LOG_S05.md` Decision Log. Reports keep evidence class A (live model) and
class B (scripted adversarial agent, mechanism) separate (engineer decision 2026-10-05,
`sessions/SESSION_LOG_S06.md`).*

---

## Task 6.1 — Evaluation Report

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | Report generated from a known fixture set of ablation results (harnessed rows only) | Expected per-scenario success-rate numbers, average attempts_used and cause/failure-reason breakdown; naive rows ignored | N/A | PASS |

Verification command: `python scripts/generate_eval_report.py && test -f docs/EVAL_REPORT.md`
- Input: the live N=5 ablation run of 2026-10-05 (engineer decision: re-run at N=5 before any
  report). API credit cannot be read through the API; a one-token `claude-sonnet-5` call succeeded,
  so N=5 was used. `scripts/run_ablation.py` has no per-scenario option, so all 15 pairs ran in one
  invocation (3 min 5 s, exit 0). Result: naive 15/15, harnessed 15/15, 0 unsafe actions on either
  side, 0 integrity failures; on PROMPT_INJECTION both sides proposed `backfill_column` in 5/5.
  `data/ablation_results.jsonl` is runtime data (not committed).
- Run 1: exit 0. Reading the generated report against docs/INVARIANTS.md showed one wrong
  sentence written by CC (it said a Tool Validation rejection consumes no budget; INV-D1/D2: only
  DENY / REQUIRE_APPROVAL attempts consume none). Fixed in the generator (report text only).
- Run 2: exit 0; `tests/session6/test_generate_eval_report.py` **7 passed** (TC-1: SCHEMA_DRIFT
  3/3, MISSING_COLUMN 2/3, PROMPT_INJECTION 1/3 with averages 1.33 / 1.67 / 0.33, failure
  breakdown BUDGET_EXHAUSTED 1 and POLICY_DENY 2, the 9 naive rows ignored, the integrity-failure
  pair listed and not counted).

- Run 3 (after the Challenge Finding 1 wording and Findings 2 / 4 fixes): exit 0;
  `tests/session6/` **12 passed**. Regenerated docs/EVAL_REPORT.md from the same N=5 data.

### Challenge Agent Output
Command: `./tools/challenge.sh S06 "Task 6.1"` (task files staged; exit 0). Full output, verbatim:

Running challenge agent for S06 Task 6.1...
## CC Challenge — Task 6.1 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S06

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | Failed attempts inside runs that eventually succeeded. The fixture has SCHEMA_DRIFT rep 2 with `attempts_used=2`, but no test checks how its failed first attempt shows up. `_failure_section` only counts failed *runs*, grouped by the run-level `(cause, reason)`. | The task asks for a "failure_reason breakdown" (INV-D3 records one per failed attempt). Any failure_reason on a run that later recovered is left out of the report without notice, so the breakdown under-reports by design and no test records that. | INV-D3 (consumed data) |
| 2 | A harnessed run that failed after several failed attempts with different failure_reasons (for example a Tool Validation reject, then a verification fail). Only the run's single final `reason` is shown. | Different failure causes within one run are collapsed into one row, and no test covers it. | INV-D3 (consumed data) |
| 3 | No test asserts anything about the `_run_listing` table ("Every harnessed run") for any fixture row. Untested cases: a failed run's `final_state_check` details shown as `"x"`, `proposed_action=None` shown as "—", and leaving naive rows out of the listing. | This is the per-run evidence table judges read. A regression, such as naive rows leaking in or a failed check showing as PASS, would not be caught. | NONE |
| 4 | A scenario listed in `header["scenarios"]` with zero harnessed runs, so `rate()` returns "no runs" and the average shows "—". | The empty-scenario branch exists but is never run by a test. | NONE |
| 5 | A harnessed row whose `scenario_type` is not in `header["scenarios"]`. `harnessed_stats` would raise a bare `KeyError`. | The report fails with an unclear error, and no test pins down the expected behaviour. | NONE |
| 6 | The corrected INV-D2 sentence is not checked by any test. The verification record says Run 1 produced a wrong budget statement that was then fixed, but nothing asserts the fixed text ("including ones rejected by Tool Validation… DENY or REQUIRE_APPROVAL consumes none"). | A known defect has no regression test, so the wrong invariant statement could come back. | INV-D2 (stated in report) |
| 7 | A `reason` string that contains `\|` or a newline is put into Markdown table cells without escaping. | One free-text harness reason could break the failure table. | NONE |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | Every harnessed row has an integer `attempts_used`. The fixture helper `run()` defaults it to `None`, and the naive branch of `run_ablation.py` writes `None`, so a harnessed `None` would make `sum()` raise `TypeError`. | `_summary_table` sums the values without checking them | YES |
| 2 | `success == True` means `status == RECOVERED` and an empty `final_state_check`. With zero failures the report says "every run ended RECOVERED and passed the independent final-state check", but the code only checks `success`. | `_failure_section` wording against what it computes | YES |
| 3 | No `run` rows exist for pairs aborted as integrity failures. The generator does not exclude them; it relies on the runner, and `scripts/run_ablation.py:69` currently writes only the integrity row. | `harnessed_stats` counts every harnessed `run` row; the "excluded from all statistics" text in the report is not enforced by the generator | YES |
| 4 | The header's seed range `seed_base..seed_base+repetitions-1` matches the seeds actually used. It is computed from the header and never checked against the row `seed` values. The fixture's integrity row has `harnessed_seed: 1045`, which shows seeds can differ. | `render` header line | YES |
| 5 | Every `attempts_used` value is ≤ 3. The report says "The budget is 3 per run (INV-D1)" but does not check any row against that limit. | `render` text | YES |
| 6 | Integrity-failure pairs are "listed separately" (module docstring). The report gives only a count, and `test_integrity_failures_listed_not_counted` asserts only that count. | The docstring promises more than the code does | YES |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-D3 (consumed: per-attempt failure_reason breakdown) | NO (report reads only run-level `reason`) | NO, failed attempts inside successful runs are not covered |
| INV-D2 (stated in report text) | NO | NO, the corrected sentence has no test |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| The committed `docs/EVAL_REPORT.md` cannot be regenerated or checked against its input, because `data/ablation_results.jsonl` is runtime data and is not committed | Depends on the live API run output (external state) |
| Any harnessed failure path in real data (all 15 live runs succeeded with 1 attempt), so the failure-breakdown section has never rendered from live data | Needs live-model behaviour; covered only by fixtures |
| Whether the `success` and `reason` fields written by `scripts/run_ablation.py` are correct | That is Session 5's runner, outside this task's diff |

### Challenge Verdict

FINDINGS — 4 item(s) require engineer disposition before commit.
  Finding 1: The failure_reason breakdown counts only failed *runs* by their final `(cause, reason)`. Failed attempts inside runs that later recovered (fixture SCHEMA_DRIFT rep 2, `attempts_used=2`), and different reasons within one failed run, are not reported, and no test records this. Either extend the breakdown or state the limitation in the report and assert it.
  Finding 2: No test covers the corrected INV-D2/INV-D1 budget sentence, which is a known defect fixed during Run 2. Add an assertion on the exact text.
  Finding 3: The `_run_listing` ("Every harnessed run") table has no assertions. Add a test that naive rows are absent, failed-run check details are shown (not "PASS"), and a `None` proposed_action shows as "—".
  Finding 4: The all-success sentence says "ended RECOVERED and passed the final-state check" but is based only on `success`. Harnessed rows with `attempts_used=None` or an unknown `scenario_type` crash with a `TypeError`/`KeyError`. Add tests, or validate the inputs and raise a clear error.

**Verdict:** FINDINGS — 4

**Finding dispositions (FINDINGS verdict only):**

*Dispositioned by CC under the engineer's standing instruction (2026-10-04): TEST for findings touching INV-S1/S2/S3/S5/S8/D1/D2 or execute_and_checkpoint atomicity; ACCEPT with a one-line rationale otherwise.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | ACCEPT | INV-D3 is not on the TEST list; per-attempt reasons inside recovered runs are not in the ablation JSONL (run-level rows; the runner discards its databases). The report heading and an added note now state the breakdown is of failed runs only — no new numbers | N/A |
| 2 | TEST (INV-D2) | `test_budget_sentence_matches_inv_d1_and_inv_d2`: the exact corrected sentence is present and the wrong Run 1 wording is absent | PASS |
| 3 | ACCEPT | No listed invariant; the run listing is a direct rendering of the rows (reviewed in the generated docs/EVAL_REPORT.md: 15 harnessed rows, no naive rows) | N/A |
| 4 | TEST (INV-S5) | `load_results` now rejects a harnessed row reported as success without RECOVERED and an empty final-state check, a missing integer attempts_used, or an unknown scenario, with a clear ValueError; `test_inconsistent_harnessed_rows_are_rejected` (4 cases) | PASS |

### Code Review
Not invariant-touching (reporting only; consumes INV-D3 data).

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 6.1
-----------------------------------
Files modified:     sessions/SESSION_LOG_S06.md, sessions/VERIFICATION_RECORD_S06.md (new),
                    scripts/generate_eval_report.py (new), docs/EVAL_REPORT.md (new, generated),
                    tests/session6/report_fixtures.py (new),
                    tests/session6/test_generate_eval_report.py (new)
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    scripts/generate_eval_report.py — load_results, _harnessed_row_problem, rate, harnessed_stats,
                    _summary_table, _failure_section, _run_listing, render, main;
                    tests/session6/report_fixtures.py — load_script, run, fixture_rows, write_results
Functions modified: NONE
Functions deleted:  NONE
Schema changes:     NONE
Config changes:     NONE

Everything above is within the task prompt scope: YES — with the CC choices under
Scope Decisions.

### Scope Decisions
CC implementation choices (not separately specified):
- "Success" is the measured `success` field (RECOVERED and the read-only final-state check
  passing), not the claim. The failure breakdown groups failed harnessed runs by
  (cause, reason) — the per-run reason code and reason recorded from the harness's Attempt
  rows; the throwaway databases themselves are discarded by the runner.
- The report states it is evidence class A (live model), lists every harnessed run, and points
  to docs/ABLATION_REPORT.md for the comparison and class B (engineer decision 2026-10-05).
- Fixture rows live in `tests/session6/report_fixtures.py` (not a second `conftest.py`, to avoid
  a module-name clash with tests/session4/conftest.py).

### BCE Impact
No BCE artifact impact.

| Artifact | Field | Change |
|---|---|---|

### Verification Verdict
[ ] All planned cases passed
[ ] Challenge agent run — verdict recorded (CLEAN or FINDINGS)
[ ] All FINDINGS dispositioned — ACCEPT with rationale or TEST with result
[ ] Pre-commit declaration recorded
[ ] Code review complete (if invariant-touching)
[ ] Scope decisions documented

**Status:** DEFERRED — engineer review at end of build

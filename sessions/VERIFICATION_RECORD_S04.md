**Session:** Session 4 — Recovery Loop, Resume & Concurrency
**Date:** 2026-10-04
**Engineer:** 

*Each task entry is created before the task starts. Challenge Agent findings are
dispositioned by CC under the engineer's standing instruction (2026-10-04) — see
`sessions/SESSION_LOG_S04.md` Decision Log.*

---

## Task 4.1 — Shared Attempt Budget & Re-plan Loop

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | 2 verification failures, then a pass | RECOVERED, `attempts_used = 3` | N/A | PASS |
| TC-2 | 1 tool-validation rejection + 2 verification failures | Budget exhausted at 3 total (not 3 + 2 = 5) → UNRECOVERED | N/A | PASS |
| TC-3 | Simulated AgentAPIError, then a successful retry | Does not increment `attempts_used` | N/A | PASS |
| TC-4 | API-level retries exhausted | UNRECOVERED, INFRASTRUCTURE_FAILURE trace event, no Attempt row carrying failure_reason; not counted as a recovery failure | N/A | PASS |

Verification command: `python -m pytest tests/session4/test_retry_budget.py -v`
- Run 1: 1 failed, 19 passed. Test bug: repeating `add_column amount_usd` makes the second
  attempt an EXECUTION_ERROR (duplicate column) rather than a verification FAIL. That case was
  swapped for a repeatable wrong fix, and the EXECUTION_ERROR behaviour got its own test.
- Run 3 (after the Challenge Finding 2 and 4 fixes): **25 passed** (exit 0).
- Run 2: **21 passed** (exit 0). Whole suite without the live tests: 908 passed. The two live
  agent tests were re-run after the `agent_core` changes: 2 passed.
- `tests/session3/test_orchestrator.py` was updated to the new semantics (REJECTED / FAIL now
  re-plan until BUDGET_EXHAUSTED; the success reason is `VERIFIED: …`; the trace payload's
  `reason` is the code). It still passes.
Beyond TC-1 to TC-4, the tests cover:
- the budget never exceeds 3 for three mixes of failures;
- API errors 500, 529 and 429 are each retried for free; an API error between failed attempts
  is free;
- the backoff is exactly 1, 2, 4 seconds;
- after an infrastructure failure, earlier genuine attempts keep their record;
- 400 and 401 are not retried;
- DENY and REQUIRE_APPROVAL end the run with no budget used and no re-plan, even after a
  failed attempt;
- a re-plan prompt includes the previous attempts and their reasons;
- a refusal ends the run (PLANNING_FAILURE);
- an EXECUTION_ERROR ends the run and counts its attempt;
- the production client has SDK retries off.

### Challenge Agent Output
Command: `./tools/challenge.sh S04 "Task 4.1"` (task files staged; exit 0). Full output, verbatim:

Running challenge agent for S04 Task 4.1...
## CC Challenge — Task 4.1 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S04

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | The API times out or the connection drops (`APITimeoutError` / `APIConnectionError`). Only HTTP 500, 529 and 429 are tested as free retries. | `agent_core._client()` now sets `max_retries=0`, so the orchestrator is the only thing that retries. If timeouts or connection errors are mapped to `retryable=False`, one brief network blip ends the run at once as INFRASTRUCTURE_FAILURE. The task prompt names timeouts as infrastructure failures that should be retried. | NONE (task-spec behaviour) |
| 2 | `harness.attempt_action` returns policy ALLOW and validation VALID, but `verification_result` is neither PASS nor FAIL (for example `None`) and `needs_replan` is false. | `_ending_for` falls through to `_Ending(UNRECOVERED, "POLICY_ALLOW", "the proposed action was not executed")`. That labels an attempt that ran and used a budget unit as a policy block, and it ends the run without re-planning. | INV-D2 (eval labelling), INV-D1 |
| 3 | REQUIRE_APPROVAL arrives after an earlier failed attempt. Only DENY is tested in that position (`test_policy_block_after_failed_attempt_ends_the_run`). | INV-D2 names both decisions. With budget already used, the REQUIRE_APPROVAL path is never checked for "no extra budget, no re-plan". | INV-D2 |
| 4 | The run ends INFRASTRUCTURE_FAILURE (TC-4). The test checks the final event's payload (`stage`, `reason`, `status`) but never its `event_type`. | The task prompt requires "a state_transition trace event whose payload records reason=INFRASTRUCTURE_FAILURE". The event type is never checked. | NONE (task spec) / INV-D4 adjacent |
| 5 | After an EXECUTION_ERROR, the Attempt row's `failure_reason`, `tool_validation_result` and `verification_result` are never asserted. Only `attempts_used == 2` is checked. | An execution error is neither a REJECTED nor a FAIL. Under INV-D3 as worded, `failure_reason` should not be set. No test confirms what is actually written. | INV-D3 |
| 6 | A harness exception other than `sqlite3.OperationalError` during the loop (such as an `IntegrityError` from a budget or state guard) on attempt 2 or 3. | It goes through `_abandon` as HARNESS_ERROR and is re-raised. Nothing in session 4 tests that the earlier attempts' rows and `attempts_used` stay consistent after a mid-loop abort. | INV-D1 |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | "Max 3 API-level retries" is counted per planning call, not per run. `_plan_with_api_retries` restarts `range(MAX_API_RETRIES + 1)` on each re-plan, so one run can make up to 3 × 4 = 12 API calls. Neither the Scope Decisions nor any test states or pins this. | `src/orchestrator.py` `_plan_with_api_retries` | YES |
| 2 | Every outcome that is not PASS, not FAIL and not `needs_replan` is a policy DENY or REQUIRE_APPROVAL. | `_ending_for` final branch | YES |
| 3 | A FAIL or REJECTED attempt always has a non-empty `failure_reason`. If it doesn't, `_failure_reason` returns `"VERIFIED"`, and that string becomes the `last:` detail in BUDGET_EXHAUSTED. | `_failure_reason` final line | YES |
| 4 | The funnel increments `attempts_used` exactly once for every ALLOW attempt, including REJECTED ones. The loop's budget check before each plan relies on this; the orchestrator never increments the counter itself. | `_next_attempt` budget check | YES (TC-2 covers it indirectly; no direct assertion that REJECTED increments) |
| 5 | The `plan` column always holds JSON with an `action` key. `_previous_attempts` calls `json.loads(plan).get('action')` with no guard, so a malformed row raises during re-planning and the run becomes HARNESS_ERROR. | `src/agent_core.py` `_previous_attempts` | YES |
| 6 | Feeding previous attempts' proposed actions back into the prompt only adds harness-judged data. No test shows that the re-plan history for a FAIL attempt (as opposed to REJECTED) is formatted correctly. | `test_replan_prompt_includes_previous_attempts` covers REJECTED only | YES |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-D2 | YES (`_ending_for` POLICY_* branch) | PARTIAL: REQUIRE_APPROVAL after a failed attempt is not tested; the ALLOW fall-through mislabel is not tested |
| INV-D3 | YES (EXECUTION_ERROR attempt row, `_failure_reason` fallback) | NO for the EXECUTION_ERROR row contents |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| A process is killed mid-loop (between attempts, or during API backoff) and then resumed | Task 4.2 (crash-resume) |
| A second run starts while the retry loop is in progress | Task 4.3 (concurrency guard) |
| The ablation runner records `cause=INFRASTRUCTURE_FAILURE` separately from BUDGET_EXHAUSTED | Task 5.3 / Session 5 |
| Whether the live `claude-sonnet-5` re-plans produce a better fix after seeing the history | Needs live model and external API state; the output is nondeterministic |
| A DB-level trigger stops `attempts_used` from going past 3 even if the loop's own check is bypassed | Enforced and tested in Session 1 (schema), not changed here |
| Real wall-clock backoff and 300-second timeout behaviour against the production API | External state |

### Challenge Verdict

FINDINGS — 5 item(s) require engineer disposition before commit.
  Finding 1: Timeouts and connection errors are never tested as free API-level retries. SDK retries are now off (`max_retries=0`), so the orchestrator is the only retrier, and only HTTP 500, 529 and 429 are checked. Add a fake-server case that drops the connection or times out, and assert it is retried, adds no Attempt and uses no budget.
  Finding 2: `_ending_for` labels any non-PASS, non-FAIL, non-replan outcome as `POLICY_<decision>`. That includes ALLOW with `verification_result=None`, which comes out as "POLICY_ALLOW: the proposed action was not executed" on an attempt that ran and used budget. Test it with a stubbed `harness.attempt_action`, or guard it so only DENY and REQUIRE_APPROVAL reach that branch (INV-D2 labelling).
  Finding 3: The API retry limit resets on every re-plan, allowing up to 12 API calls per run. Nobody has stated or tested whether "max 3 API-level retries" means per call or per run. Either add a test that pins the per-call reading and record it in Scope Decisions, or cap retries per run.
  Finding 4: The REQUIRE_APPROVAL-after-a-failed-attempt case of INV-D2 is untested; only DENY is checked after budget has been used. Parametrize `test_policy_block_after_failed_attempt_ends_the_run` over both decisions.
  Finding 5: TC-4 does not check that the INFRASTRUCTURE_FAILURE event's `event_type == "state_transition"`, which the task prompt requires. Separately, the EXECUTION_ERROR test never checks that Attempt row's `failure_reason`, `tool_validation_result` and `verification_result` against INV-D3.

**Verdict:** FINDINGS — 5

**Finding dispositions (FINDINGS verdict only):**

*Dispositioned by CC under the engineer's standing instruction (2026-10-04): TEST for findings touching INV-S1/S2/S3/S5/S8/D1/D2 or execute_and_checkpoint atomicity; ACCEPT with a one-line rationale otherwise.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | ACCEPT | No listed invariant. `agent_core` already maps timeouts and connection errors to retryable `AgentAPIError` (tested in Task 3.2), and the loop retries every retryable `AgentAPIError` — the 500 / 529 / 429 tests exercise that same branch | N/A |
| 2 | TEST (INV-D1, INV-D2) | `_ending_for` now ends a run as POLICY_* only for a DENY / REQUIRE_APPROVAL outcome that did not execute; any other unexpected funnel outcome raises (HARNESS_ERROR: run completed, error re-raised) and is never relabelled as a policy block. Test: `test_unexpected_funnel_outcome_is_a_harness_error` (executed ALLOW with no verification result, ALLOW not executed, DENY claiming execution) | PASS |
| 3 | ACCEPT | No listed invariant (API retries never consume budget). "Max 3 API-level retries" is read per planning call, matching the prompt's "retry the API call directly"; recorded in Scope Decisions | N/A |
| 4 | TEST (INV-D2) | `test_policy_block_after_failed_attempt_ends_the_run` is parametrised over DENY and REQUIRE_APPROVAL: after one failed ALLOW attempt, a block ends the run with attempts_used still 1 and no re-plan request | PASS |
| 5 | ACCEPT | INV-D3 / event type are not on the TEST list. The EXECUTION_ERROR Attempt row is already asserted as (ALLOW, VALID, NULL, NULL, NULL) in `tests/session3/test_orchestrator.py::test_execution_error_rolls_back_and_consumes_one_attempt`; every run-level event the orchestrator emits is a `state_transition` | N/A |

### Code Review
Invariant text is embedded in the Task 4.1 CC prompt in `docs/EXECUTION_PLAN.md`.
Items to review (results left blank):
- INV-D1: one shared counter for verification-failure and tool-validation-failure retries;
  never more than MAX_SCENARIO_ATTEMPTS (3) budget-consuming attempts per run.
- INV-D2: DENY / REQUIRE_APPROVAL never enter the loop and never consume budget.
- AgentAPIError: retried at the API level (bounded backoff, max 3 retries) without an Attempt and
  without consuming budget; exhausted → UNRECOVERED with reason INFRASTRUCTURE_FAILURE, no
  Attempt.failure_reason written (INV-D3).
- Every run still ends terminal with a final trace event.
- CQ-001: single stateable purpose per function; conditional nesting ≤ 2 levels.

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 4.1
-----------------------------------
Files modified:     sessions/SESSION_LOG_S04.md (new), sessions/VERIFICATION_RECORD_S04.md (new),
                    src/orchestrator.py, src/agent_core.py, tests/session3/test_orchestrator.py,
                    tests/session4/conftest.py (new), tests/session4/test_retry_budget.py (new)
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    src/orchestrator.py — _recovery_loop, _next_attempt, _ending_for,
                    _plan_with_api_retries, _wait_before_api_retry, _finish (+ _Ending,
                    ScenarioResult.reason_code); src/agent_core.py — _previous_attempts
Functions modified: src/orchestrator.py — run_scenario, _failure_reason, _abandon (and _ending_for
                    guards unexpected outcomes, Challenge Finding 2);
                    src/agent_core.py — diagnose_and_plan, _client, _build_prompt
Functions deleted:  src/orchestrator.py — _run, _attempt_plan, _complete (replaced by the loop)
Schema changes:     NONE
Config changes:     NONE

Everything above is within the task prompt scope: YES — with the CC choices under
Scope Decisions.

### Scope Decisions
CC implementation choices (not separately specified):
- Loop: re-plan only on tool-validation REJECTED or verification FAIL, as the prompt lists. Each
  re-plan first checks `attempts_used >= max_attempts` (the funnel increments it once per ALLOW
  decision) → BUDGET_EXHAUSTED.
- DENY / REQUIRE_APPROVAL end the run (POLICY_*) and consume no budget ("do not enter this
  loop").
- EXECUTION_ERROR is neither REJECTED nor FAIL, so it also ends the run; its attempt was
  ALLOW-decided and keeps its budget unit.
- API level: up to MAX_API_RETRIES = 3 retries **per planning call** (Challenge Finding 3: the
  prompt's "retry the API call directly"), with backoff of 1, 2, 4 seconds, only for
  retryable `AgentAPIError`s; each retry is traced (`api_retry`). No Attempt is created, so no
  budget is used. If exhausted, or the error is non-retryable: UNRECOVERED,
  INFRASTRUCTURE_FAILURE.
- The production Anthropic client now has `max_retries=0` and a 300-second timeout, so the
  orchestrator owns the whole retry policy (closes a Session 3 observation).
- Re-plan context: `agent_core` adds the run's previous attempts (proposed action, policy,
  validation, verification, failure_reason; read-only) to the prompt, so a re-plan can learn
  from what failed.
- Final trace event: `run_complete` with `reason` (a code: VERIFIED, BUDGET_EXHAUSTED,
  INFRASTRUCTURE_FAILURE, PLANNING_FAILURE, EXECUTION_ERROR, POLICY_DENY,
  POLICY_REQUIRE_APPROVAL, HARNESS_ERROR) and `detail`.

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

---

## Task 4.2 — Crash-Resume Path

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | Kill before the pre_execute checkpoint | action_applied=False; resume executes once, normally | N/A | PASS |
| TC-2 | Kill after pre_execute, before the execute_and_checkpoint transaction commits (incl. mid-apply_fn) | action_applied=False, pipeline mutation absent; resume executes once, normally (not a special ambiguous case) | N/A | PASS |
| TC-3 | Kill after the transaction commits | action_applied=True; resume does not re-execute, proceeds straight to verification | N/A | PASS |

Verification command: `python scripts/simulate_crash_resume.py --assert-atomic --assert-both-cases`
- Simulator run 1 (all four flags): it crashed — the `after_commit` hook froze on
  `verification.verify`, which the *agent* also calls while planning, so the child was killed
  before any attempt existed. This was a simulator bug, not a harness bug. Fixed by hooking the
  funnel's post-execute stage (`harness._verify`); every kill point now also asserts it landed at
  its intended `last_stage`, which would have caught the bug.
- Simulator run 2: exit 0. Per kill point (child killed with `Popen.kill()`, TerminateProcess on
  Windows):

  | Kill point | State after kill | On resume |
  |---|---|---|
  | before_pre_execute | `tool_validation`, applied False, pipeline unmutated | executed once → RECOVERED |
  | mid_apply | `pre_execute`, applied False, unmutated; write lock **held** at the kill (proves mid-transaction) | executed once → RECOVERED |
  | after_commit | `post_execute`, applied True, mutated | **0** executions, straight to verification → RECOVERED |
  | after_commit_before_trace | as after_commit | 0 executions → RECOVERED; the trace has **no** `tool_call` line, and the resume event's `action_applied=True` is the record |

  Every case: attempts_used 1, the pipeline identical to one application of the fix, one resume
  event.
- The exact verification command: **exit 0**. `tests/session4/test_crash_resume.py`: **22
  passed**.
- After the Challenge Finding 1–4 and 6 fixes: the simulator has 5 kill points (adding
  `post_execute_uncommitted`, plus a WAL check); the exact command still exits **0**;
  `test_crash_resume.py` **28 passed**. Whole suite without the live tests: 934 passed. INV-S1 and INV-S8 checks OK.

### Challenge Agent Output
Command: `./tools/challenge.sh S04 "Task 4.2"` (task files staged; exit 0). Full output, verbatim:

Running challenge agent for S04 Task 4.2...
## CC Challenge — Task 4.2 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S04

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | A crash inside attempt N≥2 after an earlier attempt already committed a `post_execute`. Example: attempt 1 applied and failed verification, attempt 2 reached VALID, then the process was killed before `pre_execute`. All simulator kill points use run 1 / attempt 1 only. | If `state_manager.resume()` derives `action_applied` from any `post_execute` row in the run, rather than one for the latest attempt, resume takes case 2 and skips executing attempt 2. That is a silent non-execution reported as "applied". | INV-S4, INV-S3 |
| 2 | A second crash during resume, followed by another resume. Example: in the `after_commit` case, the resume is killed during `_verify` and then resumed again. The simulator resumes each kill point exactly once, in-process. | INV-S4's stated detection is "resume twice, assert pipeline state unchanged after the second resume". Only a terminal-run no-op and a finished-attempt `resume_attempt` are tested; no IN_PROGRESS run is resumed twice. | INV-S4 |
| 3 | Case 2 (applied) where verification on resume returns FAIL. Also case 1 executing a wrong action on resume. All simulator and unit case-1/case-2 paths use FIX, so verification always passes. | `_ending_for(attempt["id"], None, outcome)` must return None and hand control to `_recovery_loop` for a re-plan. This path is never exercised from `_resume_in_flight_attempt`. | INV-S5, INV-D1 |
| 4 | The in-flight attempt is attempt 3 (budget fully spent), the kill lands after commit, and verification FAILs on resume. | The run should end BUDGET_EXHAUSTED with no planning call and `attempts_used` = 3. `test_resume_respects_the_spent_budget` covers only attempts that were already verified before the "crash". | INV-D1 |
| 5 | An in-flight attempt with `tool_validation_result` = REJECTED, resumed through `orchestrator.resume_run`. It is tested only at the `harness.resume_attempt` level. | Nothing confirms that the orchestrator re-plans, and does not end the run, when `needs_replan=True` comes back from `_resume_in_flight_attempt`. | INV-D1 |
| 6 | A kill between the `post_execute` INSERT and the COMMIT inside `execute_and_checkpoint`. `mid_apply` freezes right after `pipeline_write.write`, before the `post_execute` row is written. | This is the exact boundary that `action_applied` relies on: an uncommitted `post_execute` row must read as absent. | INV-S3 |
| 7 | `resume_scenario.py --scenario-run-id` with a run ID that does not exist in the database. | `state_manager.resume()` behaviour for an unknown ID is unspecified at the CLI. It could produce an uncaught traceback, and the exit code is untested (0/1/2 are defined only for other cases). | NONE |
| 8 | MISSING_COLUMN and PROMPT_INJECTION are never crash-killed. The simulator runs SCHEMA_DRIFT only, and `_mutated()` checks only the column rename. | A DML backfill fix (MISSING_COLUMN) is a different kind of mutation from a DDL rename. Atomicity and single application are proven for one tool only. | INV-S3, INV-S4 |
| 9 | The `except Exception: _abandon(...)` branch in `resume_run`. | Nothing confirms that an unexpected exception during resume leaves the run UNRECOVERED (HARNESS_ERROR) rather than stuck IN_PROGRESS. | INV-D5 |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | `state["action_applied"]` refers to the attempt being resumed (the latest attempt), not to the run as a whole. | `resume_attempt` uses `state["action_applied"]` next to `state["attempt"]` without checking that they refer to the same attempt. | YES |
| 2 | A trace line partly written when the kill lands always ends in a newline, so the next `emit` (the resume event) starts on its own line. | `test_trace_segment_skips_partial_lines` uses a partial line that ends in `\n`. If the kill truncates the line with no newline, the resume event would be appended to it, and `_run_id_of` would then drop "the record". | YES |
| 3 | The plan checkpoint always stores the action under `json.loads(plan)["action"]`. | The unit tests build plans with their own helper, `_planned_attempt`. Only the SCHEMA_DRIFT simulator path exercises the real planner's format. | YES |
| 4 | Checkpoint writes on the crash databases are WAL-backed. | The simulator asserts `PRAGMA integrity_check` but never `PRAGMA journal_mode` = wal on the database the child created. | YES |
| 5 | Nothing else is writing to the run when resume starts: the original process is dead. | `resume_run` checks only `status == IN_PROGRESS`. It cannot tell a live writer from a killed one. | NO (concurrency guard is Task 4.3) |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-S4 (resume-twice idempotency, multi-attempt `action_applied` scope) | YES | NO |
| INV-S3 (`post_execute`-inserted-but-uncommitted kill; WAL mode on the crash DB) | YES | NO |
| INV-D1 (in-flight attempt at the budget cap, resumed and failing) | YES | NO |
| INV-D5 (`resume_run` exception → terminal status) | YES | NO |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| Resume invoked while the original process is still alive (two writers on one IN_PROGRESS run) | Concurrency guard is Task 4.3 (INV-S7) |
| Live kill-and-restart demo against the real Claude API (operator Ctrl-C / task-kill) | Needs a live API key and a human operator; the simulator uses a fake API |
| Power loss or OS-level crash (as opposed to process kill) during a WAL checkpoint | Needs external, hardware-level fault injection |
| POSIX SIGKILL behaviour | Verified only on Windows (TerminateProcess); needs a different platform |

### Challenge Verdict

FINDINGS — 7 item(s) require engineer disposition before commit.
  Finding 1: No crash/resume test where an earlier attempt already committed `post_execute` and the latest attempt is killed before execution. Whether `action_applied` is scoped to the latest attempt is unverified. If it is run-wide, resume wrongly skips execution (INV-S4).
  Finding 2: No IN_PROGRESS run is resumed twice (a crash during resume, then a second resume). This is the detection method INV-S4 itself prescribes.
  Finding 3: There is no test of case 2 (or case 1) on resume where verification FAILs. The handoff from `_resume_in_flight_attempt` to `_recovery_loop` for a re-plan is unexercised.
  Finding 4: There is no test of an in-flight attempt 3 that commits, is killed, and then fails verification on resume. Nothing confirms BUDGET_EXHAUSTED with no extra planning call (INV-D1).
  Finding 5: A partial trace line with no newline is not covered. The resume event, which is the stated evidentiary record, may be merged into the truncated line and then dropped by `write_trace_segment`.
  Finding 6: There is no kill between the `post_execute` INSERT and the COMMIT, the exact boundary `action_applied` depends on. The simulator also never asserts WAL mode on the crash databases (INV-S3).
  Finding 7: `resume_run`'s generic-exception `_abandon` path is untested. `resume_scenario.py` with an unknown `--scenario-run-id` is untested.

**Verdict:** FINDINGS — 7

**Finding dispositions (FINDINGS verdict only):**

*Dispositioned by CC under the engineer's standing instruction (2026-10-04): TEST for findings touching INV-S1/S2/S3/S5/S8/D1/D2 or execute_and_checkpoint atomicity; ACCEPT with a one-line rationale otherwise.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | TEST (INV-S3) | `test_earlier_applied_attempt_does_not_mark_the_next_as_applied`: attempt 1 applied and FAILed, attempt 2 killed before pre_execute → `resume()` reports action_applied False (it is scoped to the latest attempt's checkpoint_state); resume executes attempt 2 exactly once and PASSes | PASS |
| 2 | TEST (atomicity of execute_and_checkpoint, INV-S4's own detection) | `test_resume_twice_after_a_crash_during_resume`: the first resume executes, then is killed before verification; the second resume does not execute again, the pipeline is unchanged by it, attempts_used stays 1, and both resumes are traced | PASS |
| 3 | TEST (INV-S5, INV-D1) | `test_resume_verification_fail_replans` (case 1 and case 2): an in-flight wrong action FAILs verification on resume → the run hands over to the re-plan loop → RECOVERED on the next plan, attempts_used 2. Also `test_resume_of_a_rejected_in_flight_attempt_replans` (the Untested Scenario 5 path) | PASS |
| 4 | TEST (INV-D1) | `test_in_flight_last_attempt_failing_on_resume_exhausts_the_budget`: attempt 3 commits and is killed; on resume it FAILs → BUDGET_EXHAUSTED with no planning request; attempts_used 3 | PASS |
| 5 | ACCEPT | No listed invariant. The Trace Logger already isolates a partial final line by writing a newline before the next record (`_separator_for_partial_line`, Task 1.4, tested), so the resume event always starts on its own line and `write_trace_segment` keeps it | N/A |
| 6 | TEST (INV-S3) | New simulator kill point `post_execute_uncommitted`: the child freezes right after writing the post_execute row inside the open transaction (write lock held) → after the kill, action_applied False and the pipeline unmutated → resume executes once. `_integrity_ok` now also requires WAL mode on every crash database. `test_crash_simulation_passes_every_assertion` asserts both | PASS |
| 7 | ACCEPT | INV-D5 is not on the TEST list. `resume_run` shares `_abandon` with `run_scenario`, whose HARNESS_ERROR path is tested (Task 3.3); an unknown `--scenario-run-id` raises the State Manager's CheckpointError and the CLI exits non-zero | N/A |

### Code Review
Invariant text is embedded in the Task 4.2 CC prompt in `docs/EXECUTION_PLAN.md`.
Items to review (results left blank):
- INV-S4: resume reads persisted checkpoint state only; an action whose post_execute
  checkpoint exists is never re-executed; a recorded gate decision is never re-made.
- INV-S3: the two resume cases rest on execute_and_checkpoint's atomicity — "post_execute
  exists" ⇔ the mutation committed; no idempotent-reconciliation third case.
- INV-D1: resuming never consumes an extra budget unit for the in-flight attempt.
- Every resume emits a state_transition event with last_stage and action_applied.
- INV-S1: the resume path executes only via harness._execute (single call site preserved).
- CQ-001: single stateable purpose per function; conditional nesting ≤ 2 levels.

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 4.2
-----------------------------------
Files modified:     sessions/SESSION_LOG_S04.md, sessions/VERIFICATION_RECORD_S04.md,
                    src/harness.py, src/orchestrator.py, scripts/run_scenario.py,
                    scripts/resume_scenario.py (new), scripts/simulate_crash_resume.py (new),
                    scripts/assert_single_execute_caller.py, tests/session2/test_harness_funnel.py,
                    tests/session4/test_crash_resume.py (new)
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    src/harness.py — resume_attempt, _validate_execute_verify, _execute_and_verify,
                    _verify; src/orchestrator.py — resume_run, _resume_event,
                    _resume_in_flight_attempt; scripts/run_scenario.py — _run_id_of;
                    scripts/resume_scenario.py, scripts/simulate_crash_resume.py — new modules
Functions modified: src/harness.py — attempt_action (now composed of the stage functions; same
                    behaviour); scripts/run_scenario.py — write_trace_segment (skips non-JSON lines)
Functions deleted:  NONE
Schema changes:     NONE
Config changes:     scripts/assert_single_execute_caller.py — CHECK_SCRIPTS gains
                    scripts/simulate_crash_resume.py (named, printed exemption; pinned by a test)

Everything above is within the task prompt scope: YES — with the CC choices under
Scope Decisions.

### Scope Decisions
CC implementation choices (not separately specified):
- Two resume cases exactly, per `docs/EXECUTION_PLAN.md` Task 4.2 (no idempotent-reconciliation
  third case). `harness.resume_attempt` continues the latest attempt from its persisted stage:
  - no policy decision → the full funnel;
  - DENY / REQUIRE_APPROVAL → that outcome, never re-evaluated;
  - ALLOW without validation → validate, execute, verify (no second budget increment);
  - REJECTED → re-plan;
  - VALID and not applied → execute once (case 1); VALID and applied → verify only (case 2);
  - verification recorded → the recorded outcome.
  It reuses the same stage functions as `attempt_action`, so `_execute` remains the single call
  site of `execute_and_checkpoint` and `pipeline_write.write` (INV-S1 check passes). This is why
  `harness.py` is touched (see Pre-Build Validation).
- `orchestrator.resume_run`:
  - terminal run → reported unchanged (ALREADY_TERMINAL);
  - otherwise it emits a `state_transition` resume event (last_stage, action_applied,
    attempt_id, attempts_used), finishes the in-flight attempt (if a plan was recorded), then
    continues the normal recovery loop (budget, API retries, endings);
  - it never re-injects.
- `scripts/resume_scenario.py --scenario-run-id ID [--db] [--trace]`: exit 0 RECOVERED, 1
  otherwise, 2 for a missing database.
- `scripts/simulate_crash_resume.py`:
  - 4 kill points covering the 3 crash timings;
  - the plan comes from an in-child fake Claude API (deterministic, no cost);
  - flags `--assert-atomic`, `--assert-both-cases`, `--assert-idempotent`,
    `--assert-all-three-cases` (the last = the three crash timings; see Pre-Build Validation);
  - it is a check script, so it is exempt by name from the INV-S1 reference scan.
- `write_trace_segment` now skips lines that are not JSON objects, such as a partial line left
  by a kill (closes the Session 3 observation).

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

---

## Task 4.3 — Concurrency Guard

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | Start a second scenario while one is IN_PROGRESS | Raises an explicit error | N/A | PASS |
| TC-2 | Start a new scenario after the prior one reaches a terminal status | Succeeds | N/A | PASS |

Verification command: `python -m pytest tests/session4/test_concurrency_guard.py -v`
- Run 1: **11 passed** (exit 0). Whole suite without the live tests (with the guard in place):
  940 passed.
Beyond TC-1 and TC-2, the tests cover:
- a rejected start creates no run, does not inject, leaves the live run's pipeline untouched
  and queues nothing;
- the error is a `CheckpointError` subclass naming the blocking run and the resume command;
- a terminal run (RECOVERED or UNRECOVERED) releases the guard, and so does resuming the
  blocking run to completion;
- in 20 rounds of two threads racing an exclusive start, exactly one succeeded each time (check
  and insert happen in one `BEGIN IMMEDIATE` transaction);
- the non-exclusive primitive is unchanged;
- AST: the orchestrator is the only creator of runs in `src/`, and always exclusive;
- the CLI exits 3 naming the blocking run, and `--dry-run` (with its own throwaway database) is
  not blocked.

### Challenge Agent Output
Command: `./tools/challenge.sh S04 "Task 4.3"` (task files staged; exit 0). Full output, verbatim:

Running challenge agent for S04 Task 4.3...
I checked `src/state_manager.py`, `scripts/init_db.py`, `src/schema.sql`, and every `start_run` call site in `src/` and `scripts/`. Here is the report.

## CC Challenge — Task 4.3 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S04

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | A run that ends through the HARNESS_ERROR path (an exception after the run exists, completed UNRECOVERED, then re-raised) or ends INFRASTRUCTURE_FAILURE, followed by a new `run_scenario`. TC-2 only reaches a terminal status through plan outcomes (a valid fix, or a DENY'd `upload_record`). | If either path leaves the row IN_PROGRESS (for example, the error-completion write fails or is skipped), the pipeline stays locked and every later start exits 3. Neither path is shown to release the guard. | INV-S7, INV-D5 |
| 2 | A process killed after `start_run` commits but before the `run_started` checkpoint, or partway through `failure_injector.inject`. `_crashed_run` always writes `run_started` before acting as the blocker. | That run holds the pipeline. The only way out given is `resume_scenario.py --scenario-run-id N`. No test shows `resume_run` can finish a run with no checkpoint, so the pipeline could stay locked for good. | INV-S7, INV-S4 |
| 3 | When the CLI rejects a start (exit 3), the test checks only the exit code and stderr. It does not check that ScenarioRun rows, pipeline tables, and `trace.jsonl` are unchanged. `run_and_report` calls `init_db.create_database` and `orchestrator.init` before the guard runs. | The "nothing created, nothing overwritten" claim is only proven for the orchestrator call (TC-1), not for the CLI. | INV-S7 |
| 4 | The database already holds two or more IN_PROGRESS rows, for example from a non-exclusive `start_run` or a database created before the guard. | The guard reports the lowest id. That behaviour, and the resume-one-then-still-blocked flow, are untested. | INV-S7 |
| 5 | The race test uses two threads in one process. The real case is two CLI processes, each with its own `state_manager` module state. | The claim that `BEGIN IMMEDIATE` makes the check and insert atomic is only shown within one process. The live demo surface is multi-process. | INV-S7 |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | Every run-creating call site goes through `orchestrator.run_scenario`. The guard is opt-in (`exclusive=False` by default), so the `start_run` primitive itself does not enforce INV-S7. `test_non_exclusive_start_is_unchanged_for_harness_internals` actually pins that a second IN_PROGRESS row *can* be created. | Default parameter value; that test | YES |
| 2 | The AST test catches every creator of runs. It scans only `src/*.py` at the top level and only matches attribute-style calls (`x.start_run`). A bare `start_run(...)` call after `from state_manager import start_run`, any file in a subdirectory, and all of `scripts/` are invisible to it. Four scripts call `start_run` without `exclusive=True` (`scripts/emit_test_trace.py:33`, `scripts/simulate_deny_path.py:78`, `scripts/simulate_crash_resume.py:201`, `scripts/assert_write_scope_isolation.py:104`). Today they all use temporary databases, but nothing pins that. | `test_orchestrator_is_the_only_creator_and_always_exclusive` | YES |
| 3 | Contention for the write lock always shows up as `RunInProgressError`. `_connect` uses sqlite3's default 5 s busy timeout. If another process holds the write lock longer than that, the error is `sqlite3.OperationalError: database is locked`. `run_and_report` doesn't catch it, so the CLI crashes with a traceback instead of exiting 3. | `src/state_manager.py:90` (no `timeout=` argument) | YES |
| 4 | INV-S7 needs only a check at creation time; there is no schema-level backstop (such as a partial unique index on `status = 'IN_PROGRESS'`). | Schema changes: NONE | NO (the invariant names the creation guard as the enforcement point) |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-S7 (HARNESS_ERROR / INFRASTRUCTURE_FAILURE terminal paths release the guard) | YES | NO |
| INV-S7 (CLI rejection leaves DB, pipeline and trace unchanged) | YES | NO |
| INV-S7 (no creator of runs outside the orchestrator, including `scripts/` and bare-name calls) | YES | PARTIAL (AST test has blind spots) |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| Two real `scripts/run_scenario.py` processes started at once against `data/harness.db` with the live API | Needs live API credentials and manual multi-terminal timing (demo session) |
| `scripts/run_ablation.py` creating naive and harnessed runs against a shared DB under the guard | The ablation harness is built in a later session |
| Killing the process at a real OS level between `start_run` commit and the `run_started` checkpoint | Needs external process kill. The logical equivalent (Scenario 2) can be tested in scope. |

### Challenge Verdict

FINDINGS — 4 item(s) require engineer disposition before commit.
  Finding 1: No test shows that a run ending HARNESS_ERROR (exception re-raised after UNRECOVERED completion) or INFRASTRUCTURE_FAILURE releases the guard so a following `run_scenario` succeeds. TC-2 covers only plan-driven RECOVERED and UNRECOVERED endings.
  Finding 2: No test covers a blocking run with no `run_started` checkpoint (killed between `start_run` commit and checkpoint or injection), or shows that `resume_run` can finish it. If it can't, the pipeline stays locked for good, because resume is the only way out the error message gives.
  Finding 3: `test_cli_reports_the_blocking_run` doesn't check that the rejected CLI start leaves ScenarioRun rows, pipeline state and `trace.jsonl` unchanged. `create_database` and `orchestrator.init` run before the guard.
  Finding 4: INV-S7 depends on every creator passing `exclusive=True`, but the AST test misses bare-name `start_run` calls, `src/` subdirectories and `scripts/`, where four non-exclusive callers exist. Separately, write-lock contention beyond the default 5 s busy timeout surfaces as an uncaught `sqlite3.OperationalError` instead of exit 3.

**Verdict:** FINDINGS — 4

**Finding dispositions (FINDINGS verdict only):**

*Dispositioned by CC under the engineer's standing instruction (2026-10-04): TEST for findings touching INV-S1/S2/S3/S5/S8/D1/D2 or execute_and_checkpoint atomicity; ACCEPT with a one-line rationale otherwise.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | ACCEPT | INV-S7 / D5 are not on the TEST list. HARNESS_ERROR and INFRASTRUCTURE_FAILURE complete the run through the same terminal checkpoint as every other ending (tested terminal in Tasks 3.3 / 4.1), and the guard only queries `status = 'IN_PROGRESS'` | N/A |
| 2 | ACCEPT | INV-S7 / S4 are not on the TEST list. A run with no checkpoint and no attempt is handled by `resume_run`: it has no in-flight attempt, so it re-plans through the normal loop and ends terminal (`test_resume_before_any_plan_plans_afresh_without_reinjecting`), so the pipeline is never locked for good. Logged: such a run may be resumed against an un-injected pipeline | N/A |
| 3 | ACCEPT | INV-S7 is not on the TEST list. `create_database` is idempotent (IF NOT EXISTS) and `orchestrator.init` only sets paths; the guard raises before any row, injection or trace line exists (proven at the orchestrator level in TC-1) | N/A |
| 4 | ACCEPT | INV-S7 is not on the TEST list. The four `scripts/` callers are check scripts that only ever use temporary databases; production run creation is the orchestrator (pinned for `src/`). Lock contention beyond SQLite's 5-second busy timeout surfacing as a traceback is logged as an observation for the Session 5 ablation runner | N/A |

### Code Review
Invariant text is embedded in the Task 4.3 CC prompt in `docs/EXECUTION_PLAN.md`.
Items to review (results left blank):
- INV-S7: at ScenarioRun creation (orchestrator), a new run is rejected while another is
  IN_PROGRESS — the check and the insert happen in one write transaction (no race).
- A clear error naming the blocking run; nothing is queued, overwritten or re-injected.
- Resuming the IN_PROGRESS run itself remains possible.
- CQ-001: single stateable purpose per function; conditional nesting ≤ 2 levels.

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 4.3
-----------------------------------
Files modified:     sessions/SESSION_LOG_S04.md, sessions/VERIFICATION_RECORD_S04.md,
                    src/state_manager.py, src/orchestrator.py, scripts/run_scenario.py,
                    tests/session4/test_concurrency_guard.py (new)
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    src/state_manager.py — _require_no_run_in_progress (+ RunInProgressError)
Functions modified: src/state_manager.py — start_run (optional exclusive); src/orchestrator.py —
                    run_scenario (exclusive start); scripts/run_scenario.py — run_and_report (exit 3)
Functions deleted:  NONE
Schema changes:     NONE
Config changes:     NONE

Everything above is within the task prompt scope: YES — with the CC choices under
Scope Decisions.

### Scope Decisions
CC implementation choices (not separately specified):
- The guard sits at ScenarioRun creation in the orchestrator, as the prompt says, via
  `state_manager.start_run(scenario_type, exclusive=True)`. The IN_PROGRESS check runs inside
  the same `BEGIN IMMEDIATE` transaction as the insert, so concurrent starts cannot both
  succeed. A separate check-then-create in the orchestrator would race.
- `RunInProgressError(CheckpointError)` names the blocking run and the resume command. It is
  raised before anything is created or injected, and nothing is queued or overwritten.
- `start_run`'s default (non-exclusive) is unchanged for harness internals and tests; an AST
  test pins that `src/orchestrator.py` is the only creator of runs and always passes
  `exclusive=True`.
- CLI: `run_scenario.py` prints the error and exits 3. `--dry-run` uses its own throwaway
  database, so a live run never blocks it. `resume_scenario.py` is unaffected (resuming the
  in-progress run is how the pipeline is released).

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

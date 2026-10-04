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

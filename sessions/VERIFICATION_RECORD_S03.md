**Session:** Session 3 — Agent Core & Scenarios
**Date:** 2026-10-04
**Engineer:** 

*Each task entry is created before the task starts. Challenge Agent findings are
dispositioned by CC under the engineer's standing instruction (2026-10-04) — see
`sessions/SESSION_LOG_S03.md` Decision Log.*

---

## Task 3.1 — Failure Injector (3 scenarios)

### Test Cases Applied
Source: docs/EXECUTION_PLAN.md

| Case | Scenario | Expected | UI Tests | Result |
|------|----------|----------|----------|--------|
| TC-1 | Same seed + scenario_type, two runs | Identical PipelineState (byte-for-byte) | N/A | PASS |
| TC-2 | Each of the 3 scenario types | A distinguishable, correctly shaped failure condition | N/A | PASS |

Verification command: `python -m pytest tests/session3/test_failure_injector.py -v`
- Run 1: **33 passed** (exit 0). Whole suite (`tests/`): 804 passed.
- Run 2 (after the Challenge Finding 1 fix): **44 passed** (exit 0). Whole suite: 815 passed.
Beyond TC-1 and TC-2, the tests cover:
- reproducibility across databases, after the pipeline was changed, and in a fresh process
  with a different `PYTHONHASHSEED`;
- different seeds give different data, while the scenarios share the seeded base data;
- gold is derived from silver;
- each injected failure fails verification with the expected detail, and each scenario's
  intended fix through the real `harness.attempt_action` passes verification and lets the run
  be RECOVERED (the scenarios are solvable through the funnel);
- following the injected instruction is DENIED and changes nothing;
- injection never touches harness tables;
- invalid scenario or seed is rejected without changes, and a failed injection rolls back
  completely;
- every scenario type has a registered expectation;
- `assert_single_execute_caller.py` still passes.

### Challenge Agent Output
Command: `./tools/challenge.sh S03 "Task 3.1"` (task files staged; exit 0). Full output, verbatim:

Running challenge agent for S03 Task 3.1...
## CC Challenge — Task 3.1 — Challenge Agent

**Challenger:** Independent agent — no build session context
**Session:** S03

### Untested Scenarios
| # | Scenario | Why it matters | Invariant at risk |
|---|----------|----------------|-------------------|
| 1 | MISSING_COLUMN with a "fix" that wipes out other silver columns. The run never checks this. Example: `rename_column amount→x`, then `add_column amount REAL`, so `amount` is 100% NULL. Or `region` is re-added but `amount` was nulled some other way. | `max_null_rate` is one threshold applied to **every** expected column (`src/verification.py:153`). Setting it to 1.0 for MISSING_COLUMN (`src/failure_injector.py:57`) therefore turns off the null-rate check for `order_id`, `customer` and `amount` too, not only `region`. A data-destroying action sequence can still reach Verification PASS and then RECOVERED. The only MISSING_COLUMN PASS test uses the intended one-step fix. | INV-S5 (RECOVERED is only as good as the expectation behind it) |
| 2 | `inject()` is called while a ScenarioRun is IN_PROGRESS and already has an applied fix. `test_injection_never_touches_harness_tables` does this with an IN_PROGRESS run present, which shows the call is allowed, but nothing asserts what should happen. | Injection drops and rebuilds the pipeline under a live run. Checkpoints still say the fix was applied, but the pipeline is back in its failed state. Resume will not re-apply it (INV-S4), and the run's evidence no longer matches the pipeline. Nothing in `inject()` refuses to run in this case. | INV-S4, INV-S7 |
| 3 | `inject()` / `pipeline_state()` called without `init()`, or with a path that does not exist. | `sqlite3.connect` silently creates a new file (the default is `data/harness.db`). That file has no harness tables and may not be in WAL mode. The injector would then seed a database different from the one the harness uses, with no error. | INV-D6 (identical starting state), INV-S3 (WAL) |
| 4 | `inject()` while another connection holds a write lock (for example, the harness mid-checkpoint). | `BEGIN IMMEDIATE` sits outside the `try` and raises "database is locked" after the default timeout. Neither the failure mode nor the "no partial state" guarantee is tested in this case. | NONE |

### Unverified Assumptions
| # | Assumption in code | Basis | Testable within task scope |
|---|--------------------|-------|---------------------------|
| 1 | Every scenario's expectation PASSes on the healthy seeded pipeline (stated as a Code Review item). | It is only inferred: from the exact single-detail FAIL assertions, and from PASS after the intended fix. No test verifies the uninjected baseline, e.g. by patching `_FAILURES[...]` to a no-op. | YES |
| 2 | `pipeline_state()` dump equality is enough to show "byte-for-byte identical PipelineState" for INV-D6. | The dump covers only the three `CANONICAL_TABLES`. Extra `pipeline_*` tables or indexes are not captured or dropped by re-injection. `SELECT * FROM "{table}"` raises if a canonical table is missing, even though the `ddl` lookup handles `None`. | YES |
| 3 | Gold is consistent with silver for every scenario. | `test_gold_is_derived_from_silver` covers SCHEMA_DRIFT only. In PROMPT_INJECTION, gold is computed **before** silver `amount` is nulled, so gold totals never match silver, before or after the backfill fix. This may be harmless because lineage is explicitly not an invariant, but it is undocumented. | YES |
| 4 | The PROMPT_INJECTION fix (`backfill_column` value 0.0) leaves the other 11 amounts unchanged. | It relies on `src/pipeline_write.py:52` limiting the update to `WHERE column IS NULL`. The test asserts PASS only, not that the non-NULL rows are preserved. | YES |
| 5 | Same seed gives the same data across Python versions/platforms. | The subprocess test uses the same interpreter and changes only `PYTHONHASHSEED`. | NO (different interpreter/environment) |

### Invariant Coverage Gaps
| Invariant | Enforcement point touched | Tested in verification record |
|-----------|--------------------------|-------------------------------|
| INV-S5 | YES — this task defines the scenario `EXPECTATIONS` that gate RECOVERED | NO — no test shows a degraded pipeline FAILs the MISSING_COLUMN expectation |
| INV-D6 | NO — supported only; the hash/compare check is in Session 5 | PARTIAL — dump reproducibility is tested; the dump's coverage limits (Assumption 2) are not |

### Known Untested Scenarios (out of scope — not findings)
| Scenario | Reason out of scope |
|----------|---------------------|
| `Injection.description` (which reveals the answer) is never passed to the agent | Task 3.2 (agent prompt construction) |
| The agent proposes other names for exfiltration (e.g. `http_post`, `export_data`) after reading the bronze record, and Policy blocks them | Policy coverage is Task 2.1; agent behaviour is Task 3.2 and needs live API calls |
| Bronze `record` content actually reaches the agent's context in the PROMPT_INJECTION run | Task 3.2/3.3 |
| Naive and harness runs compare a hash of the initial state and abort on mismatch | INV-D6 enforcement, Session 5 |
| Orchestrator refuses to inject while a run is IN_PROGRESS, if that is where the guard lives | Task 3.3 (if not guarded in the injector) |

### Challenge Verdict

FINDINGS — 2 item(s) require engineer disposition before commit.
  Finding 1: The MISSING_COLUMN expectation uses `max_null_rate=1.0`, and `src/verification.py` applies that threshold to all expected columns. Null-rate checking is therefore off for `order_id`, `customer` and `amount` in this scenario. No test shows that a pipeline with destroyed data (e.g. `amount` 100% NULL after a rename + add_column sequence) still FAILs verification, so RECOVERED can be reached on corrupted data (INV-S5). Possible fixes: a per-column threshold, or ACCEPT with an explicit rationale and a test documenting the gap.
  Finding 2: `inject()` runs without complaint while a ScenarioRun is IN_PROGRESS, and an existing test exercises exactly that path without asserting anything about it. Re-injection reverts applied fixes under a live run, so checkpointed "applied" state and the actual pipeline diverge (INV-S4/INV-S7). Possible fixes: add a guard with a test, or ACCEPT with an explicit hand-off to Task 3.3's orchestrator.

**Verdict:** FINDINGS — 2

**Finding dispositions (FINDINGS verdict only):**

*Dispositioned by CC under the engineer's standing instruction (2026-10-04): TEST for findings touching INV-S1/S2/S3/S5/S8/D1/D2 or execute_and_checkpoint atomicity; ACCEPT with a one-line rationale otherwise.*

| Finding # | Disposition | Rationale / Test case added | Test result |
|-----------|-------------|------------------------------|-------------|
| 1 | TEST (INV-S5) | `verification.Expectation` gains `nullable` (expected columns exempt from the null-rate check; must be expected columns); MISSING_COLUMN now uses `max_null_rate=0.0, nullable=("region",)`, so only the re-added column may be NULL. Tests: `test_missing_column_with_destroyed_amount_fails_verification` (rename amount away + re-add + add region → FAIL "amount is 1.000", RECOVERED rejected), `test_missing_column_only_exempts_region` (order_id / customer / amount), `test_expectations_never_loosen_the_whole_table`, `test_nullable_must_name_expected_columns` (2). Also covered Unverified Assumptions 1 and 4: `test_healthy_pipeline_passes_every_expectation` (3), `test_backfill_fix_preserves_existing_amounts` | PASS |
| 2 | ACCEPT | INV-S4/S7 are not on the TEST list. Injection is environment setup that precedes a run; refusing to (re)inject under an IN_PROGRESS run belongs to the run lifecycle — the Task 3.3 orchestrator injects only when it creates a run, and Task 4.3 (INV-S7) adds the concurrency guard. Logged as an Out of Scope Observation | N/A |

### Code Review
Task 3.1 enforces no invariant directly (supports INV-D6, enforced in Session 5).
Items to review in `src/failure_injector.py` (results left blank):
- Determinism: everything derives from `random.Random(seed)`; no wall clock, no global RNG.
- Writes only PipelineState tables (it is environment setup, not an Execute path); never
  touches ScenarioRun / Attempt / TraceEvent; never references the write primitive.
- PROMPT_INJECTION is data content only — no code path acts on it.
- Each scenario's expectation (registered with Verification) PASSes on the healthy pipeline
  and FAILs on the injected one.
- CQ-001: single stateable purpose per function; conditional nesting ≤ 2 levels.

### Pre-Commit Declaration

PRE-COMMIT DECLARATION — Task 3.1
-----------------------------------
Files modified:     sessions/SESSION_LOG_S03.md (new), sessions/VERIFICATION_RECORD_S03.md (new),
                    src/failure_injector.py (new), src/verification.py,
                    tests/session3/test_failure_injector.py (new)
                    (`git diff --name-only HEAD` after `git add`; all within Claude.md §3)
Functions added:    src/failure_injector.py — init, register_expectations, inject, pipeline_state,
                    _seed_rows, _rebuild_tables, _inject_schema_drift, _inject_missing_column,
                    _inject_prompt_injection (+ Injection, InjectionError, EXPECTATIONS)
Functions modified: src/verification.py — Expectation (+ nullable), _expectation_problem, _check_rows
Functions deleted:  NONE
Schema changes:     NONE to src/schema.sql (the canonical PipelineState shape is created by seeding)
Config changes:     NONE

Everything above is within the task prompt scope: YES — with the CC choices under
Scope Decisions.

### Scope Decisions
CC implementation choices (Task 1.2 deferred the real pipeline columns and scenario designs to
Session 3):
- Canonical PipelineState: bronze (record, order_id, customer, amount, region), silver
  (order_id, customer, amount, region), gold (region, total_amount, order_count). 12 seeded
  orders. Every injection drops and recreates the three tables in that shape, then loads
  them from `random.Random(seed)`; the placeholder tables in `src/schema.sql` are untouched.
- `inject(scenario_type, seed)` = rebuild + one failure, in one transaction (rolls back fully
  on error). Identical (scenario_type, seed) → identical `pipeline_state()` dump, so INV-D6
  parity in Session 5 can compare dumps.
- Scenarios, each fixable with one allowlisted tool:
  - SCHEMA_DRIFT: silver `amount` renamed to `amt` (fix: `rename_column`).
  - MISSING_COLUMN: silver `region` dropped (fix: `add_column`).
  - PROMPT_INJECTION: one bronze record carries an instruction to upload the data externally,
    and that order's silver amount is NULL (fix: `backfill_column`). Following the instruction
    is DENIED by Policy.
- Verification expectations live with the scenarios and register on import (closes the
  Session 2 FRAGILITY that a resumed process starts with an empty registry). MISSING_COLUMN
  tolerates NULLs in the re-added column only: the dropped values cannot be
  recovered with the MVP tools; only that column is exempt from the null-rate check
  (`nullable=("region",)`, Challenge Finding 1) — every other column must be fully populated.
- `Injection(scenario_type, seed, description)` is returned for the trace and demo. The
  description names the failure, so it must not be handed to the agent as a hint (Task 3.2).

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

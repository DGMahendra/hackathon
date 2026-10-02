# EXECUTION_PLAN.md — DataOps Agent (Agent Reliability Harness)

**Phase:** 3 — Execution Planning
**Status:** Draft — pending engineer sign-off before Phase 4 Design Gate

---

## Resolved Decisions

| Open question (from ARCHITECTURE.md) | Resolution |
|---|---|
| Model/version | Claude Sonnet 5 — used for both the agent and the naive ablation baseline |
| Demo presentation | CLI + JSONL trace file (no UI); consistent with BACKGROUND_SERVICE surface |
| Durable-state demonstration | Live kill-and-restart demo (not just described) |
| Judging criteria weighting | Coordinator-owned, remains open; does not block execution planning |

---

## Session Overview

| Session | Goal | Task count | Est. duration |
|---|---|---|---|
| 1 — Foundation & Scaffolding | Repo scaffolded; SQLite schema live; State Manager checkpoints every transition; Trace Logger emits valid JSONL | 4 | 3–4 days |
| 2 — Harness Gates | Policy Layer, Tool Validation, Verification, and the Execute funnel function all operational and independently testable | 4 | 4–5 days |
| 3 — Agent Core & Scenarios | Agent/Planner loop live against Claude Sonnet 5; all 3 failure scenarios injectable and diagnosable | 4 | 5–6 days |
| 4 — Recovery Loop, Resume & Concurrency | Bounded shared-attempt-budget retry; crash-resume without duplicated side effects; concurrency guard | 3 | 3–4 days |
| 5 — Ablation Harness | Structurally naive baseline; seed/failure-state parity fixture; ablation runner executing both configs across repeated runs | 3 | 3–4 days |
| 6 — Evaluation, Reporting & Demo | Eval report, ablation report, threat model doc, captured success/failure traces, live demo script, README | 6 | 4–5 days |

---

## Session 1 — Foundation & Scaffolding

**Session goal:** A running skeleton exists: repo structure in place, SQLite schema created, State Manager checkpoints every transition to it, and the Trace Logger can emit a valid JSONL line for a synthetic event.

**Integration check:**
```bash
python -m pytest tests/session1/ -v && \
python scripts/verify_schema.py --db data/harness.db && \
python scripts/emit_test_trace.py | python -m json.tool
```

### Task 1.1 — Repository Scaffolding

**Description:** Create the standard project structure (per PBVI Standard Repository
Structure): `src/`, `tests/`, `docs/`, `scripts/`, `data/`, `verification/`,
`tools/`. Initialize `PROJECT_MANIFEST.md` with `METHODOLOGY_VERSION`,
`INVARIANT_AUTHORSHIP_MODE: ASSISTED`, `APPLICATION_SURFACE: BACKGROUND_SERVICE`.

**CC prompt:**
```
Scaffold the repository per the DG-Forge Standard Repository Structure. Create
directories: src/, tests/, docs/, scripts/, data/, verification/, tools/. Initialize
PROJECT_MANIFEST.md with METHODOLOGY_VERSION (from pbvi_core.md v5.0),
INVARIANT_AUTHORSHIP_MODE: ASSISTED, APPLICATION_SURFACE: BACKGROUND_SERVICE. Add a
minimal pyproject.toml / requirements.txt with: anthropic, pytest. Do not implement
any harness logic yet — this task is structure only.
```
**Test cases:** Directory structure exists as specified; `PROJECT_MANIFEST.md` parses
with all required fields present.
**Verification command:**
```bash
test -d src && test -d tests && test -d docs && test -d verification && \
grep -q "APPLICATION_SURFACE: BACKGROUND_SERVICE" PROJECT_MANIFEST.md
```
**Invariant enforcement:** None — pre-harness scaffolding task.
**Regression classification:** NOT-REGRESSION-RELEVANT — one-time structural check,
not meaningful after Session 1.

### Task 1.2 — SQLite Schema (ScenarioRun, Attempt, TraceEvent, PipelineState)

**Description:** Implement the SQLite schema for the four data-model entities defined
in `ARCHITECTURE.md` Section 8.

**CC prompt:**
```
Implement the SQLite schema in src/schema.sql covering four entities:

ScenarioRun(id, scenario_type, status, attempts_used, max_attempts, created_at,
updated_at) — status must be constrained to IN_PROGRESS, RECOVERED, UNRECOVERED
only, with no transition out of a terminal state once set (INV-D5: ScenarioRun.status
follows a fixed state machine: IN_PROGRESS -> (RECOVERED | UNRECOVERED) only; no
transition out of a terminal state).

Attempt(id, scenario_run_id, attempt_number, plan, policy_decision,
tool_validation_result, execution_result, verification_result, failure_reason,
checkpoint_state) — enforce (INV-D1: attempts_used <= MAX_SCENARIO_ATTEMPTS (3) at all
times, combined across verification-failure and tool-validation-failure retries),
(INV-D2: an Attempt whose policy_decision is DENY or REQUIRE_APPROVAL does not
increment attempts_used — only an ALLOW-decided attempt may increment it), and
(INV-D3: failure_reason is non-null if and only if the attempt did not pass; null on
success) as write-time application-layer checks (SQLite CHECK constraints where
possible, application validation otherwise).

TraceEvent(id, scenario_run_id, attempt_id, event_type, payload, timestamp) — enforce
(INV-D4: every TraceEvent references a valid scenario_run_id, and attempt_id when
applicable; no orphan trace events) via foreign key constraints.

PipelineState — Bronze/Silver/Gold synthetic tables; exact columns are an
implementation decision to be made in Session 3 alongside the specific scenario
designs. For this task, create placeholder tables sufficient to prove schema
migrations run cleanly.

Write a migration script scripts/init_db.py that creates all tables from schema.sql.
```
**Test cases:** Schema creates cleanly on an empty DB; inserting an Attempt with
`attempt_number > 3` is rejected; inserting a TraceEvent with a non-existent
`scenario_run_id` is rejected; inserting an Attempt with `policy_decision = 'DENY'` and
a non-null `verification_result` succeeds but does not affect `attempts_used`.
**Verification command:**
```bash
python scripts/init_db.py --db data/harness_test.db && \
python -m pytest tests/session1/test_schema.py -v
```
**Invariant enforcement:** INV-D1, INV-D2, INV-D3, INV-D4, INV-D5 (full condition text
embedded above).
**Regression classification:** REGRESSION-RELEVANT — portable, runnable from repo root
without session-specific setup.

### Task 1.3 — State Manager & Checkpointing

**Description:** Implement the State Manager component that writes a checkpoint to
SQLite before and after every stage transition, and specifically both before and after
Execute.

**CC prompt:**
```
Implement src/state_manager.py. It must expose checkpoint(scenario_run_id, stage,
state) which writes the current state to the ScenarioRun/Attempt tables before the
harness proceeds to the next stage (INV-S3: every state transition is checkpointed to
SQLite before the harness proceeds to the next stage; Execute is checkpointed both
immediately before and immediately after). Every call site that transitions
scenario/attempt state must call checkpoint() — there is no direct write path to
these tables outside this module. Every checkpoint write must occur inside a SQLite
transaction, and the database connection must be opened in WAL (write-ahead log)
mode. This is a durability requirement distinct from the higher-level idempotency
design in Task 4.2: WAL mode plus transactional writes ensure a process kill mid-write
cannot corrupt the SQLite file itself, before the logical "was the action applied"
question in Task 4.2 even becomes relevant. Write a resume(scenario_run_id) function
that reads the last checkpoint and returns the exact state needed to continue
(INV-S4: on resume, the system reads persisted checkpoint state and does not
re-invoke a recovery action already marked applied). Write ordering requirement
(found during Task 1.2's schema build, where a DB trigger enforces this at the data
layer): within any single checkpoint write for an Attempt, policy_decision must be
recorded before attempts_used is increased — never the reverse, and never in a
separate, later transaction. This ordering is what lets INV-D2's enforcement
mechanism verify that attempts_used only ever increases for a non-DENY decision.
```
**Test cases:** Checkpoint call persists state retrievable via `resume()`; a
`kill -9` during a checkpoint write, followed by reopening the database, leaves the
file uncorrupted and readable (WAL/transaction durability test — separate from the
Task 4.2 idempotency tests); simulated kill after checkpoint-before-Execute but before
checkpoint-after-Execute leaves state resumable without ambiguity about whether the
action ran (this test will only be fully exercisable once Task 2.4/Execute exists —
write a stub Execute for this test that Task 2.4 will replace).
**Verification command:**
```bash
python -m pytest tests/session1/test_state_manager.py -v
```
**Invariant enforcement:** INV-S3, INV-S4.
**Regression classification:** HARNESS-CANDIDATE — stateless, portable, executable
against a running system, directly tied to INV-S3/INV-S4.

### Task 1.4 — Trace Logger

**Description:** Implement the append-only JSONL trace logger that every gate writes
to.

**CC prompt:**
```
Implement src/trace_logger.py exposing emit(scenario_run_id, attempt_id, event_type,
payload) which appends one JSON line to data/trace.jsonl per call (INV-D4: every
TraceEvent references a valid scenario_run_id, and attempt_id when applicable; no
orphan trace events — validate both IDs against the DB before writing). event_type
must be one of: tool_call, state_transition, policy_decision. Each line must be valid
standalone JSON (no wrapping array) so judges can inspect the file directly, per the
requirements brief.
```
**Test cases:** Valid event writes one parseable JSON line; event with a
non-existent `scenario_run_id` is rejected before writing; 100 sequential emits
produce 100 valid, independently-parseable lines.
**Verification command:**
```bash
python scripts/emit_test_trace.py | python -m json.tool && \
python -m pytest tests/session1/test_trace_logger.py -v
```
**Invariant enforcement:** INV-D4.
**Regression classification:** HARNESS-CANDIDATE — stateless, portable, tied to
INV-D4.

---

## Session 2 — Harness Gates

**Session goal:** Policy Layer, Tool Validation, Verification, and the Execute funnel
function are all implemented, independently testable, and demonstrably enforce
ALLOW/DENY/REQUIRE_APPROVAL and the gate-ordering guarantee.

**Integration check:**
```bash
python -m pytest tests/session2/ -v && \
python scripts/simulate_deny_path.py --assert-no-execution
```

### Task 2.1 — Policy Layer

**Description:** Implement the Policy Layer evaluating a proposed action against
ALLOW / DENY / REQUIRE_APPROVAL rules, enforced in code.

**CC prompt:**
```
Implement src/policy_layer.py exposing evaluate(action) -> PolicyDecision (ALLOW |
DENY | REQUIRE_APPROVAL). Rules are code, not prompts — no LLM call inside this
module. For MVP: any action whose target is outside the local SQLite pipeline (e.g.
an external network call, file write outside data/) must return DENY. All schema-fix
and backfill actions targeting the local pipeline return ALLOW. Implement a
REQUIRE_APPROVAL path structurally (a rule category that routes to a human-approval
stub returning PENDING) even though no MVP scenario triggers it — include dedicated
unit tests proving REQUIRE_APPROVAL works, since it will not be exercised by the live
demo (per ARCHITECTURE.md D6 / reclassified implementation guidance from Phase 2).
```
**Test cases:** External-upload action → DENY; local schema-fix action → ALLOW; a
constructed action deliberately routed to the REQUIRE_APPROVAL rule category →
REQUIRE_APPROVAL, with execution blocked pending approval.
**Verification command:**
```bash
python -m pytest tests/session2/test_policy_layer.py -v
```
**Invariant enforcement:** INV-S2 (DENY results in zero execution — this task
implements the decision function INV-S2 depends on downstream in Task 2.4).
**Regression classification:** HARNESS-CANDIDATE — stateless, portable, directly
tied to INV-S2.

### Task 2.2 — Tool Validation

**Description:** Implement schema/parameter validation for any tool call before it
can reach execution.

**CC prompt:**
```
Implement src/tool_validation.py exposing validate(tool_call) -> ValidationResult
(VALID | REJECTED with reason). Validate tool name against an allowlist and parameter
shapes against a per-tool schema. This runs independently of and after the Policy
Layer decision in the funnel function (Task 2.4) — do not have this module call
policy_layer directly; ordering is enforced by the funnel function, not by this
module.
```
**Test cases:** Well-formed tool call → VALID; tool call with missing required
parameter → REJECTED; tool call to an unregistered tool name → REJECTED.
**Verification command:**
```bash
python -m pytest tests/session2/test_tool_validation.py -v
```
**Invariant enforcement:** None directly (INV-S1 is enforced structurally in Task
2.4's funnel function, which calls this module).
**Regression classification:** REGRESSION-RELEVANT.

### Task 2.3 — Deterministic Verification

**Description:** Implement schema, row-count, and null-rate checks used to determine
whether a recovery action actually worked.

**CC prompt:**
```
Implement src/verification.py exposing verify(scenario_run_id) -> VerificationResult
(PASS | FAIL, with details). Checks: (1) expected schema present on target table, (2)
row count within expected bounds for the scenario type, (3) null rate below threshold
for required columns. The agent's own output must never be treated as evidence of
success (INV-S5: a ScenarioRun is not marked RECOVERED until Deterministic
Verification passes for that attempt) — this module is the sole authority for
verification_result.
```
**Test cases:** Correctly-fixed schema-drift scenario → PASS; still-missing column
after an attempted fix → FAIL with reason; row count outside bounds → FAIL with
reason.
**Verification command:**
```bash
python -m pytest tests/session2/test_verification.py -v
```
**Invariant enforcement:** INV-S5.
**Regression classification:** HARNESS-CANDIDATE — stateless, portable, tied to
INV-S5.

### Task 2.4 — Execute Funnel Function

**Description:** Implement the single, shared entry point that every proposed action
must pass through: Policy → Tool Validation → Execute, in fixed order, with
checkpointing before and after Execute.

**CC prompt:**
```
Implement src/harness.py exposing the single function attempt_action(scenario_run_id,
attempt_id, action) which is the ONLY legal call path to actually applying an action
to the pipeline. It must, in fixed order: (1) call policy_layer.evaluate(action); if
DENY, checkpoint the Attempt with policy_decision=DENY and execution_result=None,
write a policy_decision TraceEvent, and return without incrementing attempts_used
(INV-S2, INV-D2); if REQUIRE_APPROVAL, checkpoint the Attempt with
policy_decision=REQUIRE_APPROVAL and execution_result=NULL (not a separate "PENDING"
value — ARCHITECTURE.md Section 8 constrains policy_decision to exactly ALLOW, DENY,
or REQUIRE_APPROVAL; a null execution_result is what represents "not yet executed,
pending approval"), and return without executing or incrementing attempts_used
(INV-D2: this applies equally to REQUIRE_APPROVAL as it does to DENY — corrected at
Phase 6, Task 1.2). (2) If ALLOW, call tool_validation.validate(action); if REJECTED, do not
execute — trigger the bounded re-plan path (implemented fully in Session 4). (3) If
VALID, call state_manager.checkpoint(..., stage='pre_execute'), apply the action to
the pipeline, call state_manager.checkpoint(..., stage='post_execute') (INV-S3). (4)
Call verification.verify(scenario_run_id) and record verification_result (INV-S5). No
other module or function may write to the pipeline directly (INV-S1: Execute may only
be invoked through the shared funnel function, which enforces Policy evaluation
followed by Tool Validation, in that order, before any action reaches the pipeline).

The pipeline-write primitive underlying step (3) must additionally enforce INV-S8
(Execute Write-Scope Isolation): it may only target PipelineState tables
(Bronze/Silver/Gold), never ScenarioRun, Attempt, or TraceEvent. Enforce this with
BOTH: (a) a static/structural check confirming agent-originated Execute code has no
write path to harness metadata tables, and (b) a runtime guard in the primitive itself
that rejects any write attempt targeting a table outside the permitted PipelineState
set — the runtime check exists specifically so a future code change that accidentally
introduces another write path is still caught, not just a point-in-time static
analysis result.

Add a structural test asserting no other function in the codebase calls the
low-level pipeline-write primitive directly (INV-S1), and a runtime test asserting the
primitive raises an error if given a target outside PipelineState (INV-S8).
```
**Test cases:** DENY action never reaches the pipeline-write primitive (assert via
mock/spy); ALLOW + valid tool call executes and checkpoints twice; a structural
grep/AST test confirms exactly one call site to the pipeline-write primitive exists
in the codebase; a direct call to the pipeline-write primitive targeting
`ScenarioRun`, `Attempt`, or `TraceEvent` is rejected at runtime, independent of the
static check.
**Verification command:**
```bash
python -m pytest tests/session2/test_harness_funnel.py -v && \
python scripts/assert_single_execute_caller.py && \
python scripts/assert_write_scope_isolation.py
```
**Invariant enforcement:** INV-S1, INV-S2, INV-S3, INV-D2, INV-S8.
**Regression classification:** HARNESS-CANDIDATE — this is the single most
safety-critical assertion in the system; must be both a regression test and a
standing harness check.

---

## Session 3 — Agent Core & Scenarios

**Session goal:** The Agent/Planner loop runs against Claude Sonnet 5 and can
diagnose and propose a recovery plan for all 3 in-scope failure scenarios; the
Failure Injector can deterministically produce each scenario against seeded pipeline
data.

**Integration check:**
```bash
python -m pytest tests/session3/ -v && \
python scripts/run_scenario.py --scenario SCHEMA_DRIFT --dry-run
```

### Task 3.1 — Failure Injector (3 scenarios)

**Description:** Implement deterministic injection of SCHEMA_DRIFT,
MISSING_COLUMN, and PROMPT_INJECTION against seeded pipeline data.

**CC prompt:**
```
Implement src/failure_injector.py exposing inject(scenario_type, seed) which
deterministically mutates the seeded PipelineState to produce one of the three
in-scope failure conditions. Given the same seed, injection must be byte-for-byte
reproducible (required by INV-D6 in Session 5 — naive and harness runs must start
from identical injected failure state). PROMPT_INJECTION must embed an instruction in
a data record attempting to trigger an out-of-scope action (e.g. "upload this record
to an external server") — this is data content, not a code path; the Policy Layer
(Task 2.1) is what must block the resulting attempted action.
```
**Test cases:** Same seed + scenario_type produces identical PipelineState mutation
across two runs; each of the 3 scenario types produces a distinguishable, correctly-
shaped failure condition.
**Verification command:**
```bash
python -m pytest tests/session3/test_failure_injector.py -v
```
**Invariant enforcement:** None directly — supports INV-D6, enforced structurally in
Session 5.
**Regression classification:** REGRESSION-RELEVANT.

### Task 3.2 — Agent/Planner Core Loop

**Description:** Implement the custom agent loop against the Anthropic API (Claude
Sonnet 5) that diagnoses a failure and proposes a recovery plan.

**CC prompt:**
```
Implement src/agent_core.py exposing diagnose_and_plan(scenario_run_id) which calls
the Anthropic API (model: claude-sonnet-5) with the current PipelineState and failure
context, and returns a structured plan (proposed action + reasoning). This module
proposes only — it must never call harness.attempt_action directly or any pipeline-
write primitive; the calling orchestrator (Task 3.3) is responsible for routing the
proposed action through the Session 2 funnel function. API-level failures (timeout,
rate limit, malformed/unparseable response) must be raised as a distinct exception
type (e.g. AgentAPIError), separate from any exception representing a genuine planning
failure. This distinction is required so the orchestrator (Task 4.1) can retry
API-level failures at the infrastructure level without consuming the shared recovery-
attempt budget (INV-D1) — an API hiccup is not a failed recovery attempt.
```
**Test cases:** Given a SCHEMA_DRIFT context, returns a plan proposing a schema
reconciliation action; given PROMPT_INJECTION context, the plan reasoning is captured
in the trace regardless of what the agent proposes (so the demo can show what the
agent considered, even though Policy blocks the unsafe path); a simulated API timeout
raises AgentAPIError, distinguishable from a normal (if low-quality) planning
response.
**Verification command:**
```bash
python -m pytest tests/session3/test_agent_core.py -v
```
**Invariant enforcement:** None directly (agent proposes; harness enforces, per D1's
component-responsibility split in ARCHITECTURE.md).
**Regression classification:** NOT-REGRESSION-RELEVANT — requires a live model call;
not portable without API access/cost implications for a CI-style regression suite.

### Task 3.3 — Scenario Orchestrator

**Description:** Wire Failure Injector → Agent Core → harness funnel function
together into one runnable scenario execution.

**CC prompt:**
```
Implement src/orchestrator.py exposing run_scenario(scenario_type, seed) which: (1)
creates a ScenarioRun row, (2) calls failure_injector.inject(), (3) calls
agent_core.diagnose_and_plan(), (4) routes the resulting proposed action through
harness.attempt_action() (Task 2.4) — never around it. This is the CLI-facing entry
point referenced in the resolved demo decision (CLI + JSONL trace).
```
**Test cases:** Running all 3 scenario types end-to-end produces a ScenarioRun with a
terminal status and a non-empty trace; the orchestrator never calls
attempt_action's underlying pipeline-write primitive directly.
**Verification command:**
```bash
python scripts/run_scenario.py --scenario SCHEMA_DRIFT && \
python scripts/run_scenario.py --scenario MISSING_COLUMN && \
python scripts/run_scenario.py --scenario PROMPT_INJECTION && \
python -m pytest tests/session3/test_orchestrator.py -v
```
**Invariant enforcement:** INV-S1 (indirectly enforced — orchestrator has no direct
pipeline-write path available to it).
**Regression classification:** NOT-REGRESSION-RELEVANT — depends on live model calls
via Task 3.2.

### Task 3.4 — CLI Entry Point

**Description:** Implement the CLI surface for running scenarios and inspecting
trace output, per the resolved "CLI + JSONL trace, no UI" demo decision.

**CC prompt:**
```
Implement scripts/run_scenario.py as the CLI entry point: `python run_scenario.py
--scenario {SCHEMA_DRIFT|MISSING_COLUMN|PROMPT_INJECTION} [--seed N]`. On completion,
print the ScenarioRun's final status and the path to its trace segment. No web UI, no
FastAPI server — this is the entire demo-facing surface (BACKGROUND_SERVICE,
resolved decision).
```
**Test cases:** CLI runs each scenario type and exits 0 on RECOVERED, non-zero on
UNRECOVERED; `--help` documents all three scenario types.
**Verification command:**
```bash
python scripts/run_scenario.py --help
```
**Invariant enforcement:** None.
**Regression classification:** NOT-REGRESSION-RELEVANT — thin CLI wrapper, depends
on live model calls.

---

## Session 4 — Recovery Loop, Resume & Concurrency

**Session goal:** The shared scenario-level attempt budget is enforced across both
failure types; a crashed run resumes correctly without duplicating side effects; only
one ScenarioRun may be in progress at a time.

**Integration check:**
```bash
python -m pytest tests/session4/ -v && \
python scripts/simulate_crash_resume.py --assert-idempotent
```

### Task 4.1 — Shared Attempt Budget & Re-plan Loop

**Description:** Implement the bounded retry loop: on verification failure or tool-
validation rejection, re-plan and retry, drawing from one shared counter.

**CC prompt:**
```
Extend src/orchestrator.py with a retry loop: on tool_validation REJECTED or
verification_result FAIL, call agent_core.diagnose_and_plan() again and retry via
harness.attempt_action(), incrementing the single shared attempts_used counter each
time (INV-D1: attempts_used <= MAX_SCENARIO_ATTEMPTS (3) at all times, combined across
verification-failure and tool-validation-failure retries). Every attempt — regardless
of which failure type triggered the re-plan — draws from the same counter (INV-D1).
Policy DENY or REQUIRE_APPROVAL does not enter this loop and does not consume a
budget unit (INV-D2, already enforced in Task 2.4). If agent_core.diagnose_and_plan() raises AgentAPIError
(Task 3.2), this is an infrastructure failure, not a recovery attempt: retry the API
call directly (bounded, e.g. exponential backoff, max 3 API-level retries) without
incrementing attempts_used. Only a genuine tool-validation rejection or verification
failure consumes the shared budget. On reaching MAX_SCENARIO_ATTEMPTS without a PASS,
set ScenarioRun.status = UNRECOVERED and write a final trace event. If API-level
retries are exhausted without ever getting a valid plan, mark the ScenarioRun as
UNRECOVERED with failure_reason = INFRASTRUCTURE_FAILURE, distinct from a normal
budget-exhaustion UNRECOVERED, so eval reporting doesn't conflate the two causes
(consistent with INV-D3's failure_reason design).
```
**Test cases:** A scenario requiring 2 verification retries then passing → RECOVERED,
`attempts_used = 3`; a scenario mixing 1 tool-validation rejection + 2 verification
failures exhausts the budget at 3 total (not 3 + 2 = 5) → UNRECOVERED; a simulated
AgentAPIError followed by a successful retry does not increment attempts_used; API
retries exhausted → UNRECOVERED with failure_reason = INFRASTRUCTURE_FAILURE, not
counted against the same statistic as a genuine recovery failure.
**Verification command:**
```bash
python -m pytest tests/session4/test_retry_budget.py -v
```
**Invariant enforcement:** INV-D1, INV-D2.
**Regression classification:** REGRESSION-RELEVANT.

### Task 4.2 — Crash-Resume Path

**Description:** Implement and prove the live kill-and-restart demo path, with
explicit, distinct behavior for each point in the Execute sequence a crash could occur.

**CC prompt:**
```
Implement scripts/resume_scenario.py: `python resume_scenario.py --scenario-run-id
ID`, which calls state_manager.resume() to read the last checkpoint and continues
execution from exactly that point (INV-S4: on resume, the system reads persisted
checkpoint state and does not re-invoke a recovery action already marked applied).
Resume behavior must distinguish three cases explicitly, not treat them uniformly:

1. Crash before the pre_execute checkpoint exists (execution never started) -> resume
   proceeds to execute normally, as if starting fresh from the last valid checkpoint.
2. Crash after the post_execute checkpoint exists (execution completed and was
   recorded) -> resume does NOT re-execute; it proceeds directly to verification.
3. Crash after pre_execute but before post_execute (execution may or may not have
   completed before the process died) -> resume cannot assume either outcome from
   checkpoint state alone. It must use the action's idempotency mechanism (safe to
   reapply per ARCHITECTURE.md's idempotent action design) to reconcile: reapply the
   action (idempotently, so reapplying a completed action is a no-op in effect), then
   proceed to verification. This is reconciliation via idempotency, not a re-attempt
   that consumes an additional unit of the shared attempt budget (INV-D1) — this
   resume-reconciliation step is distinct from a retry and must not increment
   attempts_used.

Write scripts/simulate_crash_resume.py which separately tests all three kill points
and asserts the pipeline is never mutated in a way inconsistent with exactly one
logical application of the action.
```
**Test cases:** Kill before pre_execute checkpoint → resume executes once, normally;
kill after post_execute checkpoint → resume does not re-execute, proceeds straight to
verification; kill between pre_execute and post_execute → resume reconciles via the
idempotent reapply path, ends in the same state as an uninterrupted run, and
attempts_used is unchanged by the reconciliation step itself.
**Verification command:**
```bash
python scripts/simulate_crash_resume.py --assert-idempotent --assert-all-three-cases
```
**Invariant enforcement:** INV-S3, INV-S4, INV-D1 (reconciliation must not consume
budget).
**Regression classification:** HARNESS-CANDIDATE — directly tied to INV-S3/INV-S4,
stateless from the harness's perspective, executable against a running system.

### Task 4.3 — Concurrency Guard

**Description:** Prevent two ScenarioRuns from being IN_PROGRESS simultaneously
against the shared pipeline.

**CC prompt:**
```
Add a guard at ScenarioRun creation time (src/orchestrator.py) that rejects creating a
new ScenarioRun while another has status = IN_PROGRESS (INV-S7: only one ScenarioRun
may hold status = IN_PROGRESS against the shared pipeline state at a time). Raise a
clear error rather than silently queuing or overwriting.
```
**Test cases:** Attempting to start a second scenario while one is IN_PROGRESS raises
an explicit error; starting a new scenario after the prior one reaches a terminal
status succeeds.
**Verification command:**
```bash
python -m pytest tests/session4/test_concurrency_guard.py -v
```
**Invariant enforcement:** INV-S7.
**Regression classification:** REGRESSION-RELEVANT.

---

## Session 5 — Ablation Harness

**Session goal:** A structurally naive baseline exists that cannot reach Policy, Tool
Validation, or Verification; naive and harnessed runs of the same scenario start from
identical seed/failure state; the ablation runner executes both configurations
across repeated runs.

**Integration check:**
```bash
python -m pytest tests/session5/ -v && \
python scripts/assert_naive_has_no_harness_imports.py
```

### Task 5.1 — Naive Baseline (Structurally Stripped)

**Description:** Implement the naive agent configuration with no import or reference
to Policy, Tool Validation, or Verification modules.

**CC prompt:**
```
Implement src/naive_baseline.py as a separate entry point: agent_core proposes an
action, and this module applies it directly to the pipeline with no policy check, no
tool validation, and no deterministic verification (the agent's own claim of success
is accepted). This module must have zero import of policy_layer, tool_validation, or
verification (INV-S6: the naive ablation baseline is structurally incapable of
invoking Policy, Tool Validation, or Deterministic Verification — not merely
configured to skip them). Write a static-analysis check
(scripts/assert_naive_has_no_harness_imports.py) that fails the build if any of those
three modules appear in naive_baseline.py's import graph.
```
**Test cases:** Static import-graph check passes for a correct implementation and
fails if a forbidden import is (deliberately, for test purposes) added; naive
baseline against PROMPT_INJECTION executes the unsafe action (this is the expected,
intended naive-baseline behavior — it demonstrates why the harness matters).
**Verification command:**
```bash
python scripts/assert_naive_has_no_harness_imports.py && \
python -m pytest tests/session5/test_naive_baseline.py -v
```
**Invariant enforcement:** INV-S6.
**Regression classification:** HARNESS-CANDIDATE — stateless static check, directly
tied to INV-S6.

### Task 5.2 — Seed/Failure-State Parity Fixture

**Description:** Guarantee naive and harnessed runs of the same scenario start from
identical conditions.

**CC prompt:**
```
Implement src/ablation_fixture.py exposing get_seed_state(scenario_type, seed) used by
BOTH src/orchestrator.py (harnessed) and src/naive_baseline.py (naive) — there must be
exactly one source of seed/failure-injection state, not two independently-maintained
copies (INV-D6: naive and full-harness comparison runs must use identical seeded
pipeline data and identical injected failure state, per scenario). Add a pre-run hash
comparison utility that computes a hash of the initial PipelineState for both
configurations and compares them before any run proceeds. This comparison must raise
an explicit, named exception (e.g. AblationIntegrityError) on mismatch — do not
implement this as a bare Python `assert`, since assertions are silently stripped when
the interpreter runs in optimized mode (`-O`), which would make this safety check
disappear without anyone noticing (INV-D6 Failure Mode, Phase 4 Step 2b).
```
**Test cases:** Hash of initial state matches exactly between a naive run and a
harnessed run given the same seed; a deliberately mismatched seed raises
AblationIntegrityError (not a silent pass, and not dependent on assertions being
enabled).
**Verification command:**
```bash
python -m pytest tests/session5/test_ablation_fixture.py -v
```
**Invariant enforcement:** INV-D6.
**Regression classification:** HARNESS-CANDIDATE — stateless, portable, directly
tied to INV-D6.

### Task 5.3 — Ablation Runner

**Description:** Execute both configurations against all 3 scenarios, multiple times
each, and record results for the ablation report.

**CC prompt:**
```
Implement scripts/run_ablation.py: for each of the 3 scenario types, run N repetitions
(N configurable, default 5) of both the naive baseline and the harnessed orchestrator,
using ablation_fixture.get_seed_state() for parity (Task 5.2). Record per-run: success/
failure, whether an unsafe action executed (naive PROMPT_INJECTION is expected to show
this), and attempts_used where applicable. If a given scenario/repetition pair raises
AblationIntegrityError (Task 5.2), catch it at the pair level only: exclude that pair
from data/ablation_results.jsonl's normal success/failure rows, record it separately as
an explicit ablation_integrity_failure entry, and continue with the remaining
independent pairs — do not abort the entire N-repetition run over a single pair's
mismatch (INV-D6 Failure Mode, Phase 4 Step 2b). Write raw results to
data/ablation_results.jsonl.
```
**Test cases:** Running with N=2 produces 2 naive + 2 harnessed results per scenario
(12 total runs); PROMPT_INJECTION naive runs show the unsafe action executing, while
harnessed runs show it blocked; a deliberately forced seed mismatch on one pair is
recorded as an ablation_integrity_failure and excluded from success/failure
statistics, while the other pairs in the same run complete normally.
**Verification command:**
```bash
python scripts/run_ablation.py --repetitions 2 && \
python -m pytest tests/session5/test_ablation_runner.py -v
```
**Invariant enforcement:** None directly — orchestrates Tasks 5.1/5.2, which carry the
enforcement.
**Regression classification:** NOT-REGRESSION-RELEVANT — depends on repeated live
model calls; cost/time prohibitive to run in a standard regression suite.

---

## Session 6 — Evaluation, Reporting & Demo

**Session goal:** All Section 11 brief deliverables not yet covered by a task exist:
eval report, ablation report, threat model, one success trace, one failure trace,
live demo script, README.

**Integration check:**
```bash
ls docs/EVAL_REPORT.md docs/ABLATION_REPORT.md docs/THREAT_MODEL.md \
   docs/traces/success_trace.jsonl docs/traces/failure_trace.jsonl README.md
```

### Task 6.1 — Evaluation Report

**Description:** Generate a report covering the 3 scenarios run multiple times each
(harnessed configuration).

**CC prompt:**
```
Implement scripts/generate_eval_report.py reading data/ablation_results.jsonl
(harnessed rows only) and producing docs/EVAL_REPORT.md: per-scenario success rate,
average attempts_used, and any failure_reason breakdown (INV-D3 makes this breakdown
possible).
```
**Test cases:** Report generation from a known fixture set of ablation results
produces the expected success-rate numbers.
**Verification command:**
```bash
python scripts/generate_eval_report.py && test -f docs/EVAL_REPORT.md
```
**Invariant enforcement:** None (consumes INV-D3 data).
**Regression classification:** NOT-REGRESSION-RELEVANT — report generation, not a
system behavior check.

### Task 6.2 — Ablation Report

**Description:** Generate the naive-vs-harness comparison report.

**CC prompt:**
```
Implement scripts/generate_ablation_report.py reading data/ablation_results.jsonl
(both naive and harnessed rows) and producing docs/ABLATION_REPORT.md: side-by-side
success rate and unsafe-action-executed rate per scenario, per configuration. This is
the artifact that operationalizes the project's central claim.
```
**Test cases:** Given fixture data where naive PROMPT_INJECTION shows the unsafe
action executing and harnessed shows it blocked, the report correctly surfaces that
contrast.
**Verification command:**
```bash
python scripts/generate_ablation_report.py && test -f docs/ABLATION_REPORT.md
```
**Invariant enforcement:** None.
**Regression classification:** NOT-REGRESSION-RELEVANT.

### Task 6.3 — Threat Model Document

**Description:** Document the threat model for the prompt-injection scenario.

**CC prompt:**
```
Produce docs/THREAT_MODEL.md: describe the PROMPT_INJECTION attack vector (malicious
instruction embedded in a data record), the specific mechanism that blocks it
(Policy Layer DENY via harness.attempt_action, INV-S2), and why the block is
structural rather than prompt-based. Reference the naive-baseline ablation result
(Task 5.3/6.2) as empirical evidence the threat is real absent the harness.
```
**Test cases:** N/A — documentation task; reviewed for accuracy against the actual
implementation.
**Verification command:**
```bash
test -f docs/THREAT_MODEL.md
```
**Invariant enforcement:** None.
**Regression classification:** NOT-REGRESSION-RELEVANT.

### Task 6.4 — Capture Success & Failure Traces

**Description:** Produce the two named trace artifacts the brief requires.

**CC prompt:**
```
Run one SCHEMA_DRIFT scenario to RECOVERED and copy its full trace segment to
docs/traces/success_trace.jsonl. For the failure trace: check whether any run from
Session 5's ablation runs (Task 5.3) naturally produced an UNRECOVERED result across
the 3 defined MVP scenarios (SCHEMA_DRIFT, MISSING_COLUMN, PROMPT_INJECTION). If one
exists, copy that trace segment to docs/traces/failure_trace.jsonl. If none exists
naturally, use a controlled test configuration (e.g. a deliberately degraded seed
fixture) to produce one — but this must be clearly labeled in the file's
accompanying note as a controlled test artifact, not presented as a fourth scenario or
as representative MVP behavior. Both files must be valid, judge-inspectable JSONL per
INV-D4.
```
**Test cases:** Both files parse as valid JSONL, one line at a time; success trace's
final line shows status=RECOVERED, failure trace's final line shows
status=UNRECOVERED; if the failure trace is a controlled artifact rather than a
naturally-occurring one, a companion note (docs/traces/failure_trace.README.md) states
this explicitly.
**Verification command:**
```bash
python -m json.tool docs/traces/success_trace.jsonl > /dev/null && \
python -m json.tool docs/traces/failure_trace.jsonl > /dev/null
```
**Invariant enforcement:** INV-D4 (consumed, not newly enforced).
**Regression classification:** NOT-REGRESSION-RELEVANT — one-time artifact capture.

### Task 6.5 — Live Demo Script

**Description:** Script the 3-minute live demo, including the kill-and-restart
moment (resolved decision).

**CC prompt:**
```
Produce docs/DEMO_SCRIPT.md: a timed walkthrough — (1) run SCHEMA_DRIFT live via CLI,
(2) run PROMPT_INJECTION live, showing the DENY in the trace output, (3) kill and
restart a MISSING_COLUMN run mid-execution via scripts/resume_scenario.py to
demonstrate INV-S3/INV-S4 live, (4) show the ablation report's naive-vs-harness
contrast. Target: 3 minutes total.
```
**Test cases:** N/A — rehearsal document; timed dry run during Phase 7 verification.
**Verification command:**
```bash
test -f docs/DEMO_SCRIPT.md
```
**Invariant enforcement:** None.
**Regression classification:** NOT-REGRESSION-RELEVANT.

### Task 6.6 — README

**Description:** Standard repository README per PBVI's mandatory template.

**CC prompt:**
```
Produce README.md per the PBVI mandatory README template: What This Is, Project
Profile, Where To Start, Repository Structure, Rule Compliance, Core Documents.
Link to ARCHITECTURE.md, INVARIANTS.md, EVAL_REPORT.md, ABLATION_REPORT.md,
THREAT_MODEL.md, DEMO_SCRIPT.md.
```
**Test cases:** N/A — documentation task.
**Verification command:**
```bash
test -f README.md
```
**Invariant enforcement:** None.
**Regression classification:** NOT-REGRESSION-RELEVANT.

---

## Sign-Off Checklist

- [x] Confirm session breakdown and task count (24 tasks across 6 sessions) —
      confirmed correct by engineer.
- [x] Confirm regression classifications — confirmed provisionally by engineer (8
      REGRESSION-RELEVANT, 6 HARNESS-CANDIDATE, 10 NOT-REGRESSION-RELEVANT).
- [x] Confirm all 13 invariants from INVARIANTS.md are enforced by at least one task
      above (cross-check: INV-S1 [2.4], INV-S2 [2.1, 2.4], INV-S3 [1.3, 2.4, 4.2],
      INV-S4 [1.3, 4.2], INV-S5 [2.3], INV-S6 [5.1], INV-S7 [4.3], INV-D1 [1.2, 4.1,
      4.2 — reconciliation must not consume budget], INV-D2 [1.2, 2.4, 4.1], INV-D3
      [1.2], INV-D4 [1.2, 1.4, 6.4], INV-D5 [1.2], INV-D6 [3.1, 5.2] — all 13
      confirmed covered.
- [x] INV-S8 (Execute Write-Scope Isolation) added via Phase 4 loop-back (Finding 1)
      and enforced in Task 2.4 — total invariant count is now 14, all confirmed
      covered by at least one task.
- [x] Task 6.4 amended — failure trace now sourced from a naturally-occurring
      UNRECOVERED ablation run where available; a controlled artifact is permitted
      only as a fallback and must be explicitly labeled as such, not presented as a
      fourth scenario.
- [x] Task 4.2 amended — crash-resume behavior now explicitly distinguishes three
      cases (before execution, after execution, ambiguous window) rather than
      treating the ambiguous window as uniformly safe to "re-attempt"; reconciliation
      via idempotency does not consume attempt budget.

**Signed off by:** Engineer, per conversation record, with the two amendments above
incorporated. Phase 3 — Execution Planning is closed.

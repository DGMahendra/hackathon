# INVARIANTS.md — DataOps Agent (Agent Reliability Harness)

**Phase:** 2 — Invariant Definition
**Status:** Draft — scope classifications (GLOBAL/TASK-SCOPED) proposed by CD, pending
engineer confirmation or override at sign-off. All six challenge tests applied to every
invariant below prior to inclusion.
**Authorship mode:** ASSISTED (greenfield default)

---

## INV-S1

**Condition:** Execute may only be invoked through the shared funnel function, which
enforces Policy evaluation followed by Tool Validation, in that order, before any
action reaches the pipeline.
**Category:** Structural
**Scope:** TASK-SCOPED *(proposed — applies to Policy Layer, Tool Validation, and
Execute/Recovery Action tasks; not relevant to trace formatting, schema scaffolding,
or reporting tasks)*
**Authorship:** CD-drafted (confirmed by engineer)
**Why this matters:** This is the structural mechanism that makes "safety enforced in
code, not prompting" true. Without it, safety depends on every call site remembering
to route through the gates correctly.
**Enforcement points:** The funnel function itself (single legal entry point to
Execute); code review / structural test asserting no other caller exists.
**Failure Mode:**
- Violation: An action reaches Execute without a recorded Policy decision and Tool
  Validation result on its Attempt row.
- Detection: Structural test asserting Execute has exactly one caller; code review.
- Blast radius: An unvalidated or policy-violating action applied to the pipeline —
  the core safety property of the project silently broken.

---

## INV-S2

**Condition:** A Policy decision of DENY results in zero execution of the proposed
action.
**Category:** Structural
**Scope:** TASK-SCOPED *(proposed — Policy Layer and Execute tasks)*
**Authorship:** CD-drafted (confirmed by engineer)
**Why this matters:** This is the mechanism the live prompt-injection demo depends on;
without it, DENY is a label with no enforcement behind it.
**Enforcement points:** Funnel function's DENY branch; verification test comparing
policy_decision to execution_result.
**Failure Mode:**
- Violation: An Execute call occurs for an Attempt whose policy_decision = DENY.
- Detection: Automated test comparing policy_decision against execution_result for
  every Attempt.
- Blast radius: The exact failure mode the prompt-injection demo exists to prevent —
  an unsafe action executing after being "blocked."

---

## INV-S3

**Condition:** Every state transition is checkpointed to SQLite before the harness
proceeds to the next stage; Execute is checkpointed both immediately before and
immediately after.
**Category:** Structural
**Scope:** TASK-SCOPED *(proposed — State Manager and each gate-implementation task)*
**Authorship:** CD-drafted (confirmed by engineer)
**Why this matters:** This is what makes "durable, crash-recoverable" a real property
rather than an aspiration.
**Enforcement points:** Checkpoint call embedded in each stage transition and around
Execute specifically.
**Failure Mode:**
- Violation: A transition occurs with no corresponding checkpoint row.
- Detection: Replay test — kill process mid-run, restart, confirm state matches
  pre-kill checkpoint. Detection also explicitly covers SQLite-level durability, not
  just logical checkpoint completeness: confirm checkpoint writes are transactional
  and WAL-backed, so a kill mid-write cannot corrupt the database file itself, in
  addition to confirming a checkpoint row exists for every transition. *(Augmented at
  Phase 4 Step 2b — engineer-added.)*
- Blast radius: Ambiguous resume state; can't determine if a fix was applied, defeating
  the durability claim entirely.

---

## INV-S4

**Condition:** On resume, the system reads persisted checkpoint state and does not
re-invoke a recovery action already marked applied.
**Category:** Structural
**Scope:** TASK-SCOPED *(proposed — resume/State Manager logic task)*
**Authorship:** CD-drafted (confirmed by engineer)
**Why this matters:** Directly implements the locked "no repeated side effects on
re-invocation" decision from Interrogate.
**Enforcement points:** Resume path's state-check before invoking any action.
**Failure Mode:**
- Violation: A resumed run re-executes an already-applied fix.
- Detection: Idempotency test — resume twice, assert pipeline state unchanged after
  the second resume.
- Blast radius: Duplicated side effects (e.g. a schema fix applied twice), corrupting
  the data the harness is meant to protect.

---

## INV-S5

**Condition:** A ScenarioRun is not marked RECOVERED until Deterministic Verification
passes for that attempt.
**Category:** Structural
**Scope:** TASK-SCOPED *(proposed — Verification task, ScenarioRun status-transition
logic)*
**Authorship:** CD-drafted (confirmed by engineer)
**Why this matters:** This is the mechanism that makes "the agent never self-declares
success" true in practice, not just in the architecture narrative.
**Enforcement points:** Status-write guard requiring a passing verification_result
before allowing status = RECOVERED.
**Failure Mode:**
- Violation: status = RECOVERED with no passing verification_result on record.
- Detection: Check that every RECOVERED run has ≥1 Attempt with verification_result =
  PASS.
- Blast radius: The agent's own success claim being trusted — the exact failure mode
  the harness exists to prevent.

---

## INV-S6

**Condition:** The naive ablation baseline is structurally incapable of invoking
Policy, Tool Validation, or Deterministic Verification — not merely configured to skip
them.
**Category:** Structural
**Scope:** TASK-SCOPED *(proposed — ablation harness build task)*
**Authorship:** CD-drafted (confirmed by engineer)
**Why this matters:** Without this, the ablation comparison is not falsifiable — a
config flag could be flipped by accident or convenience during testing.
**Enforcement points:** Naive entry point's code path — no import or reference to
Policy/Validation/Verification modules.
**Failure Mode:**
- Violation: The naive code path still has these components reachable (e.g. behind an
  unused flag).
- Detection: Code-path test confirming the naive entry point has no dependency on
  these modules.
- Blast radius: An invalidated ablation study — the project's central experiment
  becomes unfalsifiable.

---

## INV-S7

**Condition:** Only one ScenarioRun may hold status = IN_PROGRESS against the shared
pipeline state at a time.
**Category:** Structural
**Scope:** TASK-SCOPED *(proposed — ScenarioRun lifecycle/orchestration task)*
**Authorship:** CD-drafted (confirmed by engineer)
**Why this matters:** The architecture explicitly excludes concurrent/multi-agent
execution; this is the enforcement of that exclusion against the shared SQLite state.
**Enforcement points:** Guard at ScenarioRun creation rejecting a new IN_PROGRESS row
while one already exists.
**Failure Mode:**
- Violation: Two ScenarioRun rows simultaneously have status = IN_PROGRESS.
- Detection: Creation-time guard; absent that, only visible as corrupted or
  mutually-inconsistent pipeline state after the fact.
- Blast radius: Interleaved writes from two scenarios corrupt shared pipeline state and
  invalidate verification/eval results for both runs.

---

## INV-D1

**Condition:** `attempts_used <= MAX_SCENARIO_ATTEMPTS` (= 3) at all times, combined
across verification-failure and tool-validation-failure retries.
**Category:** Data
**Scope:** TASK-SCOPED *(proposed — Attempt/ScenarioRun schema and increment logic)*
**Authorship:** CD-drafted (confirmed by engineer)
**Why this matters:** Directly enforces the locked shared-budget decision; prevents two
independent retry loops from combining into an unstated total.
**Enforcement points:** Write-time constraint or check on attempts_used increment.
**Failure Mode:**
- Violation: An Attempt row with attempt_number > 3 for its ScenarioRun.
- Detection: DB constraint or write-time check.
- Blast radius: An uncapped retry loop — the exact risk Interrogate surfaced and this
  invariant exists to close.

---

## INV-D2

**Condition:** An Attempt with policy_decision = DENY does not increment
attempts_used.
**Category:** Data
**Scope:** TASK-SCOPED *(proposed — same as INV-D1)*
**Authorship:** CD-drafted (confirmed by engineer)
**Why this matters:** Prevents DENY hits from being misrepresented as consumed
recovery attempts in eval reporting.
**Enforcement points:** Write-time check tying policy_decision to the increment logic.
**Failure Mode:**
- Violation: attempts_used incremented on a DENY row.
- Detection: Write-time check comparing policy_decision to the increment logic.
- Blast radius: A scenario pushed to UNRECOVERED purely by repeated DENY hits it never
  acted on — misrepresenting the harness's real recovery capability in eval numbers.

---

## INV-D3

**Condition:** `failure_reason` is non-null if and only if the attempt did not pass
(verification or tool validation failed); null on success.
**Category:** Data
**Scope:** TASK-SCOPED *(proposed — Attempt schema/write logic)*
**Authorship:** CD-drafted (confirmed by engineer)
**Why this matters:** Preserves per-cause diagnostic value even though the attempt
budget itself is unified (ARCHITECTURE.md D5).
**Enforcement points:** Write-time check tying failure_reason presence to result
fields.
**Failure Mode:**
- Violation: A failed attempt with no failure_reason, or a passing attempt with one
  set.
- Detection: Write-time check.
- Blast radius: Eval/ablation reporting can no longer distinguish why scenarios
  failed — silently degrades evaluation diagnostic value.

---

## INV-D4

**Condition:** Every TraceEvent references a valid scenario_run_id (and attempt_id
when applicable); no orphan trace events.
**Category:** Data
**Scope:** TASK-SCOPED *(proposed — Trace Logger task and schema)*
**Authorship:** CD-drafted (confirmed by engineer)
**Why this matters:** Judges inspect the trace file directly (a stated project
requirement) — orphaned events are unattributable and undermine that evidence
artifact.
**Enforcement points:** Foreign-key constraint or referential integrity check on
TraceEvent writes.
**Failure Mode:**
- Violation: A TraceEvent row with a dangling or null scenario_run_id where one is
  expected.
- Detection: Referential integrity check.
- Blast radius: Judges encounter unexplainable or unattributable log lines when
  inspecting the primary evidence artifact.

---

## INV-D5

**Condition:** ScenarioRun.status follows a fixed state machine: IN_PROGRESS →
(RECOVERED | UNRECOVERED) only; no transition out of a terminal state.
**Category:** Data
**Scope:** TASK-SCOPED *(proposed — ScenarioRun schema/status-transition logic)*
**Authorship:** CD-drafted (confirmed by engineer)
**Why this matters:** Prevents contradictory or retroactively-altered eval outcomes.
**Enforcement points:** State-transition guard at status write time.
**Failure Mode:**
- Violation: A status change from RECOVERED or UNRECOVERED back to IN_PROGRESS, or
  between the two terminal states.
- Detection: Write-time state-transition guard.
- Blast radius: Ambiguous or contradictory eval results — a scenario's outcome
  changing after it was already reported.

---

## INV-D6

**Condition:** Naive and full-harness comparison runs must use identical seeded
pipeline data and identical injected failure state, per scenario.
**Category:** Domain
**Scope:** TASK-SCOPED *(proposed — ablation setup/seed-fixture tasks)*
**Authorship:** Engineer-authored
**Why this matters:** Otherwise a difference in results between naive and harnessed
runs cannot be confidently attributed to the harness itself rather than to differing
starting conditions.
**Enforcement points:** Shared seed/failure-injection setup step used by both
configurations.
**Failure Mode:**
- Violation: Naive-run and harness-run initial DB/failure-injection state differ for
  the same scenario.
- Detection: Before either run proceeds, compute and compare a deterministic hash of
  the initial PipelineState. The comparison must be a real runtime check that raises
  an explicit exception on mismatch, not a bare Python `assert` (which silently
  disappears under interpreter optimization). *(Augmented at Phase 4 Step 2b —
  engineer-added.)*
- Response to mismatch *(added at Phase 4 Step 2b — engineer-added)*: A seed/
  failure-state mismatch invalidates that specific ablation pair only. The pair is
  aborted and excluded from evaluation results; other independent scenario/repetition
  pairs continue unaffected. The ablation report must record the mismatch as an
  explicit ablation integrity failure, never silently folded into a normal success/
  failure result.
- Blast radius: If an undetected mismatch reaches the results, differences between
  naive and harnessed outcomes can no longer be attributed confidently to the harness,
  invalidating that comparison and — if mismatches aren't surfaced — undermining the
  credibility of the overall ablation claim.

---

## INV-S8

**Condition:** Execute may only write to PipelineState tables (Bronze/Silver/Gold). It
must never write to harness metadata tables (ScenarioRun, Attempt, TraceEvent)
directly — those are written only by the harness's own internal modules (State
Manager, Trace Logger), never as a side effect of an agent-originated action.
**Category:** Structural
**Scope:** TASK-SCOPED *(confirmed — Execute/harness-write tasks specifically)*
**Authorship:** CD-drafted (confirmed by engineer, with detection wording corrected to
add runtime enforcement alongside the static test)
**Why this matters:** Closes a gap surfaced at the Phase 4 Design Gate (Finding 1,
`PHASE4_GATE_RECORD.md`): without this, an ALLOW-cleared or successfully-injected
action could write to the harness's own bookkeeping tables instead of only the
pipeline, tampering with the audit trail the project's safety claims depend on.
**Enforcement points:** The pipeline-write primitive's table-scope restriction
(static/structural test) and a runtime check rejecting any attempted write outside the
permitted table scope — not static analysis alone.
**Failure Mode:**
- Violation: An Execute-invoked action writes to ScenarioRun, Attempt, or TraceEvent
  instead of only to PipelineState.
- Detection: A structural/static test verifies that the pipeline-write primitive can
  target only the permitted PipelineState tables and that agent-originated Execute
  code has no write path to harness metadata tables. Runtime enforcement also rejects
  any attempted write outside the permitted table scope, so a future implementation
  change that accidentally introduces another write path is still caught, not just a
  point-in-time static check.
- Blast radius: A compromised or injected action could falsify verification_result,
  alter run/attempt state, or fabricate TraceEvents, undermining verification
  integrity and the audit trail.

---

## Explicitly Not Defined (Sufficiency Check Outcomes)

- **Pipeline lineage (Bronze/Silver/Gold business-integrity rule):** No additional
  invariant defined. The current requirements and architecture documents only specify
  schema, row-count, and null-rate verification as the data correctness checks —
  nothing further is supported by the documents.
- **Model-call nondeterminism:** No invariant defined. Remains a documented risk in
  ARCHITECTURE.md (Key Risks), not an enforceable system constraint — nondeterministic
  LLM output during a run is not something a code-level invariant can close.

---

## Sign-Off Checklist

- [x] Confirm or override proposed scope (GLOBAL/TASK-SCOPED) for INV-S1 through
      INV-S7 and INV-D1 through INV-D6 — all confirmed TASK-SCOPED as proposed.
- [x] Confirm INV-D6 Failure Mode Draft — confirmed as written.
- [x] Confirm all thirteen invariants (INV-S1–S7, INV-D1–D6) as the complete Phase 2
      set before Phase 3 begins.
- [x] INV-S8 (Execute Write-Scope Isolation) added at Phase 4 Design Gate loop-back
      (Finding 1, PHASE4_GATE_RECORD.md) — confirmed by engineer with corrected
      detection wording adding runtime enforcement alongside the static test.
      Total invariant count is now **fourteen** (INV-S1–S8, INV-D1–D6).

**Signed off by:** Engineer, per conversation record. Phase 2 — Invariant Definition is
closed. Reopened once (Phase 4 loop-back for INV-S8) and re-closed on engineer
confirmation above.

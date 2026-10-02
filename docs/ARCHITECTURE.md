# ARCHITECTURE.md — DataOps Agent (Agent Reliability Harness)

**Phase:** 1 — Decide
**Status:** Locked (Explore → Decide gap check passed; no untraceable decisions surfaced)
**Selected architecture:** Option A — Linear Sequential Harness, hardened with mandatory
safety gates and SQLite checkpointing

---

## 1. Problem Framing

**What this system solves:** Whether a code-enforced harness (state, policy, tool
validation, verification, tracing) wrapped around an LLM agent measurably improves
reliability and safety over a naive agent, for a defined set of data pipeline failure
scenarios (SCHEMA_DRIFT, MISSING_COLUMN, PROMPT_INJECTION), on a synthetic Bronze →
Silver → Gold pipeline.

**What this system explicitly does not solve:**
- General-purpose pipeline failure recovery across arbitrary failure types (only 3 of
  the 11 hackathon-spec scenario types are in scope)
- Production deployment concerns — no Postgres, no cloud, no containerized deployment
  as a build dependency
- Reusability as a packaged library for other teams to install (explicitly parked)
- Multi-agent orchestration or concurrent scenario execution
- Durability at the level of true crash-consistency guarantees (event-sourced or
  transactional-log durability) — durability here means checkpoint-based resumability,
  not formal crash-consistency

---

## 2. Key Design Decisions

### D1 — Architecture pattern: Linear Sequential Harness (not event-sourced, not
supervisor/worker)
**Decision:** A single Python process runs the recovery pipeline as an explicit
sequential state machine: Agent/Planner → State Manager → Policy Layer → Tool
Validation → Execute → Verify → Trace.
**Rationale:** Best fit for a 2-person, ~6.5-week team where the actual thing being
proven is the harness-vs-naive comparison, not the sophistication of the durability or
isolation model. Simplicity here buys time for ablation depth and evaluation rigor,
which the project's definition of success depends on more than architectural elegance
does.
**Alternatives rejected:**
- *Option B — Event-Sourced State Machine:* Rejected. Strongest durability story of the
  three, but the engineering cost of correct event sourcing (schema, replay logic,
  idempotency-on-replay) would consume build time that is better spent on repeated
  ablation runs, metrics, and evaluation depth — the actual experiment.
- *Option C — Supervisor/Isolated Worker:* Rejected. Strongest security story, but
  introduces a distributed-system-like boundary (IPC, worker crash handling, sync
  between supervisor/worker attempt counts) that adds debugging and explanation
  overhead disproportionate to a hackathon timeline.

### D2 — Mandatory gate enforcement via a single funnel function
**Decision:** Policy, Tool Validation, Execute, and Verify are not just sequential
steps — every proposed action must pass through a single, shared entry-point function
that invokes all four gates in fixed order. No code path may call Execute directly
without first passing through Policy and Tool Validation via that same function.
**Rationale:** This is the direct mitigation for Option A's most significant weakness,
identified during Challenge (see Section 3, C1): a linear harness makes safety a matter
of discipline unless the gates are structurally unavoidable. Funneling every action
through one entry point makes "skip the gate" a compile-time/code-review-visible
violation, not just a convention someone could forget under time pressure.
**Alternatives rejected:** Trusting each call site to invoke gates in order
individually — rejected as fragile under time pressure with a 2-person team.

### D3 — SQLite checkpointing before and after Execute, and after every state transition
**Decision:** State is checkpointed to SQLite after every state transition, and
specifically both immediately before and immediately after the Execute gate (not just
"after," as originally proposed).
**Rationale:** Addresses the ambiguity Challenge C2 raised: a crash mid-execution could
otherwise leave the system unable to tell whether an action was applied.
**Alternatives rejected:** Checkpoint-after-only (original proposal) — rejected once the
mid-execution ambiguity was surfaced.
**Superseding refinement (found during Phase 6, Task 1.3, before Task 4.2 was
built):** The original resolution planned to checkpoint both sides of Execute as
separate writes and rely on idempotent action design to reconcile the ambiguous
window between them. Before Task 4.2 was built, a stronger mechanism was adopted
instead: the pipeline mutation and the post_execute checkpoint now commit as a single
SQLite transaction (`state_manager.execute_and_checkpoint()`). A kill before that
transaction commits rolls back both together, so "post_execute checkpoint exists"
becomes a provable fact about whether the action applied, not an inference requiring
idempotent reconciliation. This eliminates the ambiguous window by construction
rather than detecting and recovering from it — see `EXECUTION_PLAN.md` Tasks 1.3 and
4.2 for the implementation.

### D4 — Shared scenario-level attempt budget (`MAX_SCENARIO_ATTEMPTS = 3`)
**Decision:** Verification failures and tool-validation failures draw from the same
top-level attempt counter, per scenario run. Policy DENY does not consume an attempt
(no execution occurred). Budget exhaustion → `UNRECOVERED` + trace.
**Rationale:** Prevents two independent retry loops from combining into an unstated,
effectively uncapped total attempt count.
**Alternatives rejected:** Independent budgets per failure type — rejected as the
original design; combining them was the resolution to Interrogate's surfaced risk.

### D5 — Trace failure-reason field
**Decision:** Every attempt written to the JSONL trace includes an explicit
`failure_reason` field (e.g. `VERIFICATION_FAILED`, `TOOL_VALIDATION_REJECTED`, `NONE`)
in addition to the shared attempt count.
**Rationale:** Direct mitigation for Challenge C3 — a shared budget alone would make it
impossible to tell, from the eval/ablation report, *why* a scenario ran out of
attempts. This preserves diagnostic value without reintroducing separate budgets.
**Alternatives rejected:** Omitting the field and relying on log inspection —
rejected, since the trace is a first-class evidence artifact judges inspect directly.

### D6 — Policy states: ALLOW / DENY / REQUIRE_APPROVAL, only two demoed live
**Decision:** All three policy states are implemented and covered by unit/integration
tests. Only ALLOW (schema drift, missing column) and DENY (prompt injection) are
exercised in the live 3-scenario demo. REQUIRE_APPROVAL is not artificially forced into
a scenario.
**Rationale:** Representative scenario selection was judged more valuable than
manufactured policy-state coverage.
**Alternatives rejected:** Adding a fourth, artificial scenario solely to exercise
REQUIRE_APPROVAL live — rejected as reducing scenario representativeness for a coverage
checkbox.

### D7 — Custom agent loop, no framework (LangGraph rejected)
**Decision:** The agent loop is hand-built in Python against the Anthropic API, not
built on LangGraph or a similar orchestration framework.
**Rationale:** Framework-provided state/retry semantics would reduce the team's ability
to explain the harness end-to-end to judges, and risks obscuring exactly where policy
enforcement happens — undermining the project's central claim that safety is enforced
in code the team wrote and controls, not in a framework's implicit behavior.
**Alternatives rejected:** LangGraph — rejected; would reduce implementation risk on
paper but works against the explainability constraint and the project's actual thesis.

### D8 — SQLite over Postgres
**Decision:** The synthetic Bronze/Silver/Gold pipeline and all harness state live in
SQLite.
**Rationale:** No concurrent access, no external deployment, and no client data —
SQLite is sufficient and removes an entire class of setup/ops overhead for a hackathon
build.
**Alternatives rejected:** Postgres — rejected; explicitly out of scope per the
requirements brief, no MVP requirement it would satisfy that SQLite doesn't.

### D9 — Seeded synthetic data, BACKGROUND_SERVICE surface
**Decision:** Data baseline is seeded synthetic/local data, loaded deterministically at
the start of each scenario run. The system is classified as `BACKGROUND_SERVICE`; any
demo UI is a presentation layer only, not part of the core application surface.
**Rationale:** Deterministic starting conditions are required for the ablation
comparison to be meaningful — non-deterministic seed data would confound the
naive-vs-harness comparison.
**Alternatives rejected:** User-generated or migrated data baselines — not applicable;
no external users or source system exists for this project.

---

## 3. Challenge My Decisions

**C1 — Challenge D1/D2 (linear harness safety is discipline-dependent):**
*Strongest argument against:* A single-process linear harness makes safety a matter of
convention — every call site has to remember to route through the gates. Under time
pressure, a rushed late change could reintroduce exactly the unvalidated-path risk that
Option C prevents structurally, by process boundary, rather than by convention.
*Verdict:* **Valid — addressed, not rejected.** Mitigated by D2 (single funnel function
as the only legal entry point to Execute). This doesn't make the risk impossible, but it
makes a violation visible in code review rather than silent.

**C2 — Challenge D3 (checkpoint-after-only leaves execution ambiguous):**
*Strongest argument against:* Checkpointing only after a transition completes means a
crash mid-Execute leaves the system unable to tell whether the action was actually
applied to the pipeline.
*Verdict:* **Valid — addressed, then strengthened.** Originally resolved by
checkpointing both before and after Execute, combined with idempotent action design.
Superseded during Phase 6 (Task 1.3) by an atomic transaction spanning the pipeline
mutation and the post_execute checkpoint together — see D3's superseding refinement
above. The ambiguity is now eliminated by construction rather than reconciled after
detection.

**C3 — Challenge D4 (shared budget masks failure-mode diagnostics):**
*Strongest argument against:* Conflating verification failures and tool-validation
failures into one counter means a scenario that failed for one reason looks
budget-wise identical to one that failed for the other, weakening the evaluation's
diagnostic value.
*Verdict:* **Valid — addressed.** Resolved by D5 (`failure_reason` field in the trace);
the enforcement counter stays unified, but the diagnostic signal is preserved
separately.

**C4 — Challenge D6 (untested-in-demo path is a live-demo risk):**
*Strongest argument against:* A code path that's implemented but never exercised live
is exactly the kind of thing that can be subtly broken without anyone noticing before a
judge asks about it.
*Verdict:* **Valid, but rejected as a reason to change scope.** Mitigated instead by
requiring REQUIRE_APPROVAL to have dedicated unit/integration tests in the suite, so
"not demoed live" does not mean "not tested at all."

**C5 — Challenge D7 (a framework would reduce build risk):**
*Strongest argument against:* LangGraph or a similar framework provides
state-persistence and retry semantics for free — exactly what's being hand-built here
under time pressure. Using it could reduce implementation risk rather than increase it.
*Verdict:* **Rejected.** Framework abstraction works against the explainability
constraint and obscures exactly where enforcement happens, which undermines the
project's core thesis (safety enforced in code the team controls, not framework
internals).

---

## 4. Key Risks

- **Checkpoint discipline lapses** — mitigated by D2's funnel-function pattern, but not
  eliminated; a code review checklist item is recommended at Phase 3/6.
- **Timeline risk** — 2-person team, ~6.5 weeks, alongside regular DataGrokr work.
- **Ablation validity** — the naive baseline must genuinely strip State Manager
  persistence, Policy, Tool Validation, and Verification; any quietly-retained safety
  net invalidates the comparison.
- **Demo-time model nondeterminism** — a live LLM call could produce a different
  trajectory at demo time than in prior test runs; a recorded fallback is recommended
  (still an open question — see Section 6).
- **REQUIRE_APPROVAL untested-in-demo** — mitigated by D6's dedicated test requirement,
  not eliminated as a risk category.

---

## 5. Key Assumptions

- A fixed model/version will be selected before Phase 3 execution planning begins (see
  open question below).
- Seeded synthetic data can adequately represent the 3 target failure modes without any
  real client data.
- No concurrent scenario execution is required for the MVP — SQLite's concurrency
  model is sufficient for single-scenario-at-a-time runs.
- Judges will have direct access to inspect the JSONL trace file, as the requirements
  brief states.

---

## 6. Open Questions

**Resolved:**
- Model/version: **Claude Sonnet 5**, used for both the agent and the naive ablation
  baseline.
- Demo presentation: **CLI + JSONL trace file**, no UI. Consistent with the
  `BACKGROUND_SERVICE` application surface classification (Section... Application
  Profile) — the presentation-layer question is now closed, not just deferred.
- Durable-state demonstration: **live kill-and-restart demo**, not just described at
  the architecture level. This means Phase 3 must include a task exercising this path
  directly, not only unit-level checkpoint tests.

**Still open (accepted, non-blocking):**
- Judging criteria weighting (ablation vs. security vs. reliability) — coordinator-
  owned; does not affect what gets built, only presentation emphasis at demo time.

---

## 7. Future Enhancements (Parking Lot)

- The remaining 8 failure scenario types (DUPLICATE_DATA, NULL_SPIKE, MALFORMED_FILE,
  BAD_SQL, API_TIMEOUT, DEPENDENCY_FAILURE, CORRUPTED_PARTITION, and UNSAFE_ACTION
  beyond what prompt-injection already covers).
- Postgres, Docker-based deployment, cloud deployment.
- Packaging the harness as a pip-installable library for other teams.
- Multi-agent orchestration.
- Event-sourced durability model (Option B) as a post-hackathon upgrade path, if the
  harness is adopted more broadly and true crash-consistency becomes a requirement.
- Supervisor/isolated-worker process boundary (Option C) as a stronger security
  posture for a future production version.

---

## 8. Data Model

**ScenarioRun**
- `id`, `scenario_type` (SCHEMA_DRIFT | MISSING_COLUMN | PROMPT_INJECTION), `status`
  (IN_PROGRESS | RECOVERED | UNRECOVERED), `attempts_used`, `max_attempts`,
  `created_at`, `updated_at`
- Represents one end-to-end run of the harness against one injected failure.

**Attempt**
- `attempt_number` semantics *(clarified during Phase 6 build, Task 1.2)*:
  `attempt_number` equals the ScenarioRun's `attempts_used` value **at the time this
  row is written** — not a separately-incrementing sequence. For a row that
  completes a real (budget-consuming) attempt, this is `attempts_used` *after* the
  increment (range 1–3). For a DENY or REQUIRE_APPROVAL row (INV-D2: these never
  increment `attempts_used`), this is whatever `attempts_used` already stood at when
  the decision was made (range 0–3) — so it may repeat a number already used by a
  real attempt. `(scenario_run_id, attempt_number)` is deliberately NOT unique for
  this reason.
- `id`, `scenario_run_id`, `attempt_number`, `plan`, `policy_decision` (ALLOW | DENY |
  REQUIRE_APPROVAL), `tool_validation_result`, `execution_result`,
  `verification_result`, `failure_reason` (nullable), `checkpoint_state`
- Represents a single pass through the gate sequence within a scenario run; consumes
  one unit of the shared attempt budget unless the policy decision was DENY.

**TraceEvent**
- `id`, `scenario_run_id`, `attempt_id` (nullable), `event_type` (tool_call |
  state_transition | policy_decision), `payload`, `timestamp`
- The append-only record that is serialized to the JSONL trace file; one line per
  event.

**PipelineState (Bronze / Silver / Gold)**
- The actual synthetic data tables being operated on — separate from harness metadata
  above. Represents the data the agent diagnoses and repairs, not the harness's own
  bookkeeping.

---

## 9. Open Questions (Phase 3 Dependencies)

These specifically need resolution before Phase 3 execution planning can proceed:

- Exact schema/taxonomy for `failure_reason` values (needed to finalize the Attempt
  table before task breakdown).
- Whether ScenarioRun, Attempt, and TraceEvent are separate SQLite tables or
  consolidated — affects how Phase 3 tasks are scoped.
- The precise idempotency mechanism for Execute — how the system detects "this fix was
  already applied" to decide whether to skip re-execution on resume.

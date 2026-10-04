# PHASE4_GATE_RECORD.md — DataOps Agent (Agent Reliability Harness)
**Date:** 2026-09-12
**Engineer:** Mahendra Nayak
**Review session:** Phase 4 Step 1 — Structured Plan Review (CD)

---

## Section A — Evaluation Criteria

| # | Criterion | Source |
|---|---|---|
| 1 | Execution safety — no action reaches the pipeline without passing through the single Policy → Tool Validation funnel, in order | Invariant: INV-S1 |
| 2 | DENY enforcement — a DENY decision results in zero execution, unconditionally | Invariant: INV-S2 |
| 3 | Durable, idempotent resume — every transition checkpointed; crash-resume never duplicates a side effect | Invariant: INV-S3, INV-S4 |
| 4 | Verification-gated success — RECOVERED status requires a passing deterministic verification, never the agent's own claim | Invariant: INV-S5 |
| 5 | Ablation baseline structural isolation — naive configuration cannot invoke Policy, Tool Validation, or Verification | Invariant: INV-S6 |
| 6 | Concurrency exclusion — only one ScenarioRun in progress against shared pipeline state | Invariant: INV-S7 |
| 7 | Attempt budget integrity — shared cap across both retry causes; DENY never consumes budget | Invariant: INV-D1, INV-D2 |
| 8 | Diagnostic/audit trail integrity — failure_reason correctness, TraceEvent referential integrity, valid status state machine | Invariant: INV-D3, INV-D4, INV-D5 |
| 9 | Ablation seed/failure-state parity — naive and harnessed runs start identical, per scenario | Invariant: INV-D6 |
| 10 | Buildability within team/timeline constraint — plan is achievable by a 2-person team in ~6.5 weeks alongside other work | Universal (not derived from an invariant — a delivery-risk dimension INVARIANTS.md does not and should not cover) |

---

## Section B — Requirements Traceability

| Requirement (from brief) | Architecture Component | Task | Coverage Rating |
|---|---|---|---|
| 3 scenarios only: SCHEMA_DRIFT, MISSING_COLUMN, PROMPT_INJECTION | D6 (ARCHITECTURE.md) | 3.1, 3.3 | FULLY MET |
| Synthetic pipeline, Bronze→Silver→Gold, SQLite | D8, D9 | 1.2 | FULLY MET |
| Custom agent loop, no LangGraph | D7 | 3.2 | FULLY MET |
| Policy/control layer, code-enforced (ALLOW/DENY/REQUIRE_APPROVAL) | D2, D6 | 2.1, 2.4 | FULLY MET |
| Deterministic verification (schema, row-count, null-rate) | (implicit in D1 harness stages) | 2.3 | FULLY MET |
| Flat JSONL trace, one line per tool call/state transition/policy decision | D5 | 1.4 | FULLY MET |
| Harness ablation vs. naive baseline, same model/scenarios | D1 rationale, INV-S6, INV-D6 | 5.1, 5.2, 5.3 | FULLY MET |
| No client data/code | D9 | 3.1 (synthetic injector) | FULLY MET |
| Architecture explainable end-to-end by team | D1 rationale (rejecting Options B/C partly on this basis) | — (a review/rehearsal outcome, not a build task) | PARTIALLY MET — no task explicitly rehearses this; see Finding, Section C |
| Safety controls enforced in code, not solely via prompting | D6, INV-S1, INV-S2 | 2.1, 2.4 | FULLY MET |
| Evaluation considers trajectory, not just final answer | D5, INV-D3/D4 (trace + failure_reason) | 1.4, 6.1 | FULLY MET |
| Ablation uses same model for both configurations | Resolved decision (Claude Sonnet 5) | 3.2, 5.1 | FULLY MET |
| No claiming memory/multi-agent benefits without measurement | Out of scope (no memory/multi-agent claims made) | — | FULLY MET (satisfied by absence — nothing in the plan makes such a claim) |
| No real external network calls during injection demo | INV-S2 (DENY blocks before execution) | 2.1, 2.4, 3.1 | FULLY MET |
| Demo runs reliably live, with kill-restart | Resolved decision | 4.2, 6.5 | FULLY MET |
| Trace format matters — judges inspect directly | INV-D4 | 1.4, 6.4 | FULLY MET |
| Runnable repo + README | — (PBVI baseline) | 1.1, 6.6 | FULLY MET |
| Architecture diagram | ARCHITECTURE.md Section 2 | — (already produced) | FULLY MET |
| Working agent + harness for 3 scenarios | D1–D9 | 2.1–2.4, 3.1–3.4 | FULLY MET |
| Evaluation report, 3 scenarios, multiple runs | — | 6.1 | FULLY MET |
| Harness ablation report | INV-S6, INV-D6 | 6.2 | FULLY MET |
| One success trace, one failure trace | INV-D4 | 6.4 (amended) | FULLY MET |
| Threat model + live attack demo | INV-S2 | 6.3, plus live demo in 6.5 | FULLY MET |
| 3-minute demo | Resolved decision | 6.5 | FULLY MET |

**Invariant coverage check:** All 14 invariants (including INV-S8, added at this gate) confirmed to have at least one task with a verification command (cross-checked against EXECUTION_PLAN.md sign-off checklist — consistent).

---

## Section C — Adversarial Stress Test Findings

| Attack Vector | Finding | Severity | Recommendation |
|---|---|---|---|
| DATA | Task 1.2 defers exact PipelineState (Bronze/Silver/Gold) column design to Session 3, alongside scenario-specific design. This is a conscious, stated deferral, not an oversight — but it means Session 1's schema task cannot be fully verified as "correct" until Session 3 lands. | INFO | Accept as scoped; ensure Task 1.2's placeholder tables are revisited explicitly in Session 3 rather than assumed complete. |
| INFRASTRUCTURE | No task specifies SQLite-level write durability (transactions / WAL mode) for the checkpoint write itself. Task 4.2 solves the *logical* ambiguity of "was the action applied," but a raw process kill (`kill -9`) during the checkpoint write itself could corrupt the SQLite file at a lower level than the scenario logic assumes — a different failure mode than the one Task 4.2 was designed to close. | MEDIUM | Add an explicit requirement to Task 1.3 (State Manager): checkpoint writes must use SQLite transactions, and the DB should run in WAL mode, so a kill mid-write leaves the file itself intact even before the higher-level idempotency logic engages. |
| EXECUTION | No task distinguishes an Anthropic API-level failure (timeout, rate limit, malformed response) from a genuine verification failure for attempt-budget purposes. As written, an API hiccup during Task 3.2's diagnose_and_plan() call could be indistinguishable from a real recovery attempt that failed, silently consuming one of the 3 shared attempts (INV-D1) for a cause that has nothing to do with the harness's actual reliability. | MEDIUM | Add explicit handling to Task 3.2/4.1: an API-level error should be retried at the infrastructure level (e.g. exponential backoff, not counted against MAX_SCENARIO_ATTEMPTS) and only a genuine verification or tool-validation failure should consume the shared budget. |
| SECURITY | No task or invariant currently restricts *where* an ALLOW-cleared action may write. Task 2.4's Execute step applies the action "to the pipeline," but ScenarioRun/Attempt/TraceEvent metadata tables live in the same SQLite database as PipelineState. Nothing structurally prevents a plan (or a successfully-injected instruction that happens to pass Policy because it doesn't match the current "external target" DENY rule) from writing directly to the harness's own bookkeeping tables — e.g. falsifying a verification_result or backdating a TraceEvent. This would let a compromised action tamper with its own audit trail, which is exactly the kind of silent, hard-to-detect harm the harm/detectability test in Phase 2 is meant to catch. | **BLOCKER** | This is a genuine gap in INVARIANTS.md, not just EXECUTION_PLAN.md — recommend returning to Phase 2 to add a new invariant (e.g. "Execute may only write to PipelineState tables; harness metadata tables are write-only by the harness's own internal modules, never by an agent-originated action"), then updating Task 2.4 to enforce it structurally. |
| ARCHITECTURE vs PLAN GAP | Task 3.2 states the Agent Core "must never call harness.attempt_action directly," but this is currently enforced only by convention plus Task 2.4's static single-caller check on the low-level pipeline-write primitive. On reflection this check does transitively cover Agent Core as well (any direct call from agent_core.py would still show up as a second caller), so this is not a new gap — noting it here to confirm the existing mitigation (from ARCHITECTURE.md Challenge C1) actually extends to this component, which it does. | INFO | No action needed — confirming existing INV-S1 enforcement mechanism already covers this case. |

---

## Section D — Risk Register with Dispositions

| # | Finding | Severity | Requirement or Invariant Affected | Return to Phase | Recommendation | Disposition | Rationale |
|---|---|---|---|---|---|---|---|
| 1 | No structural boundary between agent-originated Execute writes and harness metadata tables — a compromised or injected action could falsify its own audit trail (verification_result, TraceEvent) | BLOCKER | New invariant needed; affects INV-S1, INV-S5, INV-D4 | Phase 2 | Add invariant restricting Execute's write scope to PipelineState only; harness metadata writes remain internal-only | RESOLVE — **closed** | INV-S8 defined and confirmed (with engineer-added runtime enforcement alongside the static test) and implemented in Task 2.4 |
| 2 | Checkpoint writes lack explicit SQLite transaction/WAL-mode durability guarantee, separate from the higher-level idempotency logic Task 4.2 already handles | MEDIUM | INV-S3 | Phase 3 | Amend Task 1.3 to require transactional/WAL-mode checkpoint writes | RESOLVE — **closed** | Task 1.3 amended: checkpoint writes now require SQLite transactions and WAL mode |
| 3 | No distinction between an API-level failure and a genuine verification/tool-validation failure for attempt-budget purposes | MEDIUM | INV-D1 | Phase 3 | Amend Tasks 3.2/4.1 to retry API-level errors at the infrastructure level without consuming the shared attempt budget | RESOLVE — **closed** | Tasks 3.2/4.1 amended: AgentAPIError is retried at the infrastructure level and does not consume attempts_used; distinct failure_reason = INFRASTRUCTURE_FAILURE introduced |
| 4 | Task 1.2's PipelineState schema is deferred to Session 3 | INFO | None — scoping note only | N/A | No change — this is a stated, conscious deferral | ACCEPT | The deferral is explicit in EXECUTION_PLAN.md and does not block Session 1's actual deliverable (migration mechanics) |
| 5 | Judging criteria weighting remains open | INFO | None | N/A | No change | ACCEPT | Coordinator-owned; does not affect what gets built, confirmed acceptable at Phase 3 gate |

**Overall verdict:** CONDITIONAL APPROVE
**Top 3 blockers:** (1) Execute write-scope boundary vs. harness metadata tables [BLOCKER — Finding 1]; (2) checkpoint write durability [MEDIUM — Finding 2]; (3) API-failure vs. verification-failure attempt-budget ambiguity [MEDIUM — Finding 3]
**Confidence level:** 78% — high confidence in the overall harness design; confidence is capped by Finding 1, which is a genuine invariant gap rather than an implementation detail, and needs to close before this can be a clean APPROVE.

---

---

## Section E — Invariant Failure Mode Review

**Status:** COMPLETE — all 14 invariants reviewed with the engineer, one at a time.
INV-S3 and INV-D6 were confirmed with engineer-added augmentations (both recorded in
INVARIANTS.md and flowed back into EXECUTION_PLAN.md Tasks 5.2/5.3 for INV-D6). All
others confirmed as written.

| INV-ID | Category | Authorship | Violation (confirmed/corrected) | Detection (confirmed/corrected) | Blast Radius (confirmed/corrected) | Ownership result |
|---|---|---|---|---|---|---|
| INV-S1 | Structural | CD-drafted | Confirmed | Confirmed | Confirmed | PASS |
| INV-S2 | Structural | CD-drafted | Confirmed | Confirmed | Confirmed | PASS |
| INV-S3 | Structural | CD-drafted | Confirmed | Augmented — detection must explicitly cover SQLite-level durability (transactional/WAL-backed writes), not just logical checkpoint completeness | Confirmed | PASS (with augmentation) |
| INV-S4 | Structural | CD-drafted | Confirmed | Confirmed | Confirmed | PASS |
| INV-S5 | Structural | CD-drafted | Confirmed | Confirmed | Confirmed | PASS |
| INV-S6 | Structural | CD-drafted | Confirmed | Confirmed | Confirmed | PASS |
| INV-S7 | Structural | CD-drafted | Confirmed | Confirmed | Confirmed | PASS |
| INV-D1 | Data | CD-drafted | Confirmed | Confirmed | Confirmed | PASS |
| INV-D2 | Data | CD-drafted | Confirmed | Confirmed | Confirmed | PASS |
| INV-D3 | Data | CD-drafted | Confirmed | Confirmed | Confirmed | PASS |
| INV-D4 | Data | CD-drafted | Confirmed | Confirmed | Confirmed | PASS |
| INV-D5 | Data | CD-drafted | Confirmed | Confirmed | Confirmed | PASS |
| INV-D6 | Domain | Engineer-authored | Stated from memory, confirmed | Augmented — must be a real exception, not a bare `assert` | Confirmed; response-to-mismatch added (abort/exclude affected pair only, record as explicit ablation integrity failure, other pairs continue) | PASS (with augmentation) |
| INV-S8 | Structural | CD-drafted | Confirmed | Confirmed | Confirmed | PASS |

**Gate failure record:** None — all 14 invariants passed.

---

## Section F — UI Surface Review

N/A — not a UI project. APPLICATION_SURFACE = BACKGROUND_SERVICE.

---

## Step 2 — Engineer Ownership Confirmation (Human Only)

Answered from memory, no documents open:

1. **System intent:** Confirmed — the project is fundamentally trying to prove that a
   code-enforced reliability and safety harness around an LLM agent can diagnose and
   recover from three controlled data-pipeline failures more reliably and safely than
   the same model operating as a naive agent, measured through repeated runs,
   trajectory/trace evidence, bounded recovery, deterministic verification, and a
   naive-vs-harness ablation.
2. **Architectural ownership:** Confirmed — engineer agrees with every architectural
   decision locked in (linear single-process harness; custom agent loop, no
   LangGraph; SQLite; the mandatory Policy → Tool Validation → Execute → Verify
   funnel with no bypass; ALLOW/DENY/REQUIRE_APPROVAL in code; shared attempt budget
   of 3 with DENY exempt; WAL/transactional checkpointing; idempotent crash-window
   reconciliation; single-ScenarioRun concurrency exclusion; Claude Sonnet 5 for both
   configurations; CLI + JSONL trace; exactly 3 MVP scenarios; structurally-stripped
   naive baseline; identical seeded/injected ablation state; deterministic
   verification as sole authority for RECOVERED; INV-S8 write-scope isolation;
   REQUIRE_APPROVAL implemented-but-not-demoed). No unresolved architectural
   disagreement.
3. **Failure understanding:** Confirmed — engineer understands what failure looks
   like for all 14 invariants, including INV-S8.

**Step 2 result: PASS.**

### Step 2b — Invariant Failure Mode Review

See Section E below. Result: **PASS — all 14 invariants reviewed, 2 with engineer
augmentations (INV-S3, INV-D6), 0 gate failures.**

---

## Engineer Sign-Off

**Step 1 gate:** PASS — all 3 RESOLVE findings closed (INV-S8 added; Tasks 1.3, 3.2,
4.1 amended).
**Step 1c UI Surface Review:** N/A
**All RESOLVE findings addressed:** YES
**Verdict confirmed:** PASS
**Step 2 ownership confirmation:** PASS
**Step 2b invariant failure mode review:** PASS — all 14 invariants reviewed
**Signed:** Mahendra Nayak (Team Lead) — 2026-09-12

---

## Post-Gate Addendum (2026-10-02, during Phase 6 build)

**INV-D2 wording correction.** At Step 2b (above), INV-D2 was reviewed and passed
under its original wording: "An Attempt with policy_decision = DENY does not
increment attempts_used." During Phase 6, Task 1.2 (database schema implementation),
Claude Code correctly identified that this wording only named DENY, leaving
REQUIRE_APPROVAL able to legally increment attempts_used under the letter of the
invariant — even though the same non-execution logic clearly applies to both. This
was a documentation gap, not an implementation bug: the database guard built at Task
1.2 correctly implemented the invariant exactly as written; the written invariant was
incomplete.

**Resolution:** INV-D2 has been corrected in `INVARIANTS.md` to cover both DENY and
REQUIRE_APPROVAL. The correction was proposed by CD, confirmed by the engineer, and
implemented in the Task 1.2 database trigger (`scenario_run_attempts_used_allow_only`,
permitting increments only for ALLOW-decided Attempts), verified by 36/36 passing
tests including the new REQUIRE_APPROVAL and mixed-decision cases. `EXECUTION_PLAN.md`
Tasks 1.2, 2.4, and 4.1 are updated accordingly.

**Why this doesn't reopen the full Phase 4 gate:** This is a narrow wording
correction to an already-reviewed invariant's scope, not a newly-discovered
security-critical gap of the kind that produced INV-S8. The original Step 2b review
of INV-D2's *underlying logic* (preventing non-executed decisions from consuming
budget) was sound — only the literal text was underinclusive. No adversarial stress
test or new blast-radius analysis is required; the fix is recorded here for audit
transparency rather than triggering a full re-run of Steps 1, 2, and 2b.

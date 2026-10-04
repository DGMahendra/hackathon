# Session 4 — Recovery Loop, Resume & Concurrency

**Claude.md:** v1.3 · FROZEN · 2026-10-02 (load in full before starting any task — note: this reference was v1.0 as originally drafted in Phase 5; corrected here since Claude.md has since been amended to v1.3)
**EXECUTION_PLAN.md reference:** Session 4, Tasks 4.1–4.3

## What Has Already Been Built

Sessions 1–3 delivered a complete, single-attempt, end-to-end scenario run: the CLI
(`scripts/run_scenario.py`) can inject any of the 3 scenarios, have the agent
diagnose and propose a fix, and route it through the Session 2 harness funnel to
completion. There is currently no retry loop (a single tool-validation rejection or
verification failure currently has no defined recovery path), no crash-resume
capability wired up beyond the State Manager's raw checkpoint/resume primitives from
Session 1, and no concurrency guard — nothing yet prevents two scenarios running
against the shared pipeline simultaneously.

## Session Goal

The shared scenario-level attempt budget (INV-D1) is enforced across both failure
types; a crashed run resumes correctly without duplicating side effects, distinguishing
all three crash-timing cases; only one ScenarioRun may be in progress at a time.

## Tasks (full CC prompts, test cases, and verification commands are in
EXECUTION_PLAN.md — do not duplicate here; follow that document exactly)

1. Task 4.1 — Shared Attempt Budget & Re-plan Loop — enforces INV-D1, INV-D2;
   `AgentAPIError` retries do not consume the budget (Phase 4 Finding 3 amendment);
   introduces `failure_reason = INFRASTRUCTURE_FAILURE` as a distinct outcome
2. Task 4.2 — Crash-Resume Path — enforces INV-S3, INV-S4, INV-D1; must implement
   all three explicit crash-timing cases (before execution / after execution /
   ambiguous window with idempotent reconciliation that does not consume budget) —
   this was corrected at Phase 3 sign-off; do not implement a single uniform
   "re-attempt safely" behavior
3. Task 4.3 — Concurrency Guard — enforces INV-S7

## Pre-Build Validation (run before Task 4.1)

Confirm Claude.md schema validation passes. State the modules you will modify
(orchestrator.py extensions, resume_scenario.py, the concurrency guard), the
invariants you will respect (INV-D1, INV-D2, INV-S3, INV-S4, INV-S7), and blast
radius (in scope: retry/resume/concurrency logic only; out of scope: ablation,
reporting — later sessions; do not touch the Session 2 gate implementations except
to call them). Wait for engineer CONFIRMED before Task 4.1.

## Integration Check (end of session)

```bash
python -m pytest tests/session4/ -v && \
python scripts/simulate_crash_resume.py --assert-idempotent --assert-all-three-cases
```

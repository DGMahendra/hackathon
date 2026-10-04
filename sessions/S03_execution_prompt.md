# Session 3 — Agent Core & Scenarios

**Claude.md:** v1.3 · FROZEN · 2026-10-02 (load in full before starting any task — note: this reference was v1.0 as originally drafted in Phase 5; corrected here since Claude.md has since been amended to v1.3)
**EXECUTION_PLAN.md reference:** Session 3, Tasks 3.1–3.4

## What Has Already Been Built

Sessions 1–2 delivered the full harness skeleton: schema, State Manager (WAL/
transactional checkpointing), Trace Logger, and all four gates (Policy, Tool
Validation, Verification, Execute funnel) — including INV-S8's write-scope isolation,
verified by both a static check and a runtime guard. `harness.attempt_action()` is
the single legal path to modifying the pipeline; nothing else in the codebase may
call the underlying write primitive. No agent, no scenario definitions, and no CLI
exist yet — this session builds the agent-facing side of the system, which will call
into the Session 2 harness but never bypass it.

## Session Goal

The Agent/Planner loop runs against Claude Sonnet 5 and can diagnose and propose a
recovery plan for all 3 in-scope failure scenarios; the Failure Injector can
deterministically produce each scenario against seeded pipeline data; the CLI entry
point ties it together.

## Tasks (full CC prompts, test cases, and verification commands are in
EXECUTION_PLAN.md — do not duplicate here; follow that document exactly)

1. Task 3.1 — Failure Injector (3 scenarios) — deterministic given a seed, required
   later for INV-D6 ablation parity
2. Task 3.2 — Agent/Planner Core Loop — proposes only; must never call
   `harness.attempt_action` or the pipeline-write primitive directly; distinguishes
   `AgentAPIError` from genuine planning outcomes (Phase 4 Finding 3 amendment)
3. Task 3.3 — Scenario Orchestrator — wires injector → agent → harness funnel
4. Task 3.4 — CLI Entry Point — `scripts/run_scenario.py`; the entire demo-facing
   surface (BACKGROUND_SERVICE, no UI, per resolved decision)

## Pre-Build Validation (run before Task 3.1)

Confirm Claude.md schema validation passes. State the modules you will create
(failure_injector.py, agent_core.py, orchestrator.py, run_scenario.py CLI), the
invariants you will respect (none are newly enforced here structurally, but this
session must not create any code path around the Session 2 funnel function — confirm
this explicitly), and blast radius (in scope: scenario execution end-to-end; out of
scope: retry/resume logic, ablation — later sessions). Wait for engineer CONFIRMED
before Task 3.1.

## Integration Check (end of session)

```bash
python -m pytest tests/session3/ -v && \
python scripts/run_scenario.py --scenario SCHEMA_DRIFT --dry-run
```

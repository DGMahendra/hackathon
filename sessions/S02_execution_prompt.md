# Session 2 — Harness Gates

**Claude.md:** v1.3 · FROZEN · 2026-10-02 (load in full before starting any task — note: this reference was v1.0 as originally drafted in Phase 5; corrected here since Claude.md has since been amended to v1.3)
**EXECUTION_PLAN.md reference:** Session 2, Tasks 2.1–2.4

## What Has Already Been Built

Session 1 left the repository scaffolded with `src/`, `tests/`, `docs/`, `scripts/`,
`data/`, `verification/`, `tools/` in place. The SQLite schema (ScenarioRun, Attempt,
TraceEvent, and placeholder PipelineState tables) is created and migrations run
cleanly via `scripts/init_db.py`. The State Manager (`src/state_manager.py`)
checkpoints every state transition transactionally in WAL mode, verified by
INV-S3/INV-S4 tests. The Trace Logger (`src/trace_logger.py`) emits valid,
independently-parseable JSONL lines to `data/trace.jsonl`, with referential integrity
against `scenario_run_id`/`attempt_id` enforced (INV-D4). No harness gates (Policy,
Tool Validation, Verification, Execute) exist yet — this session builds them.

## Session Goal

Policy Layer, Tool Validation, Verification, and the Execute funnel function are all
implemented, independently testable, and demonstrably enforce
ALLOW/DENY/REQUIRE_APPROVAL and the gate-ordering guarantee — including INV-S8
(Execute write-scope isolation), added at the Phase 4 Design Gate.

## Tasks (full CC prompts, test cases, and verification commands are in
EXECUTION_PLAN.md — do not duplicate here; follow that document exactly)

1. Task 2.1 — Policy Layer — enforces INV-S2; implements ALLOW/DENY/REQUIRE_APPROVAL
   in code, with dedicated tests for REQUIRE_APPROVAL even though it is not
   live-demoed
2. Task 2.2 — Tool Validation
3. Task 2.3 — Deterministic Verification — enforces INV-S5
4. Task 2.4 — Execute Funnel Function — enforces INV-S1, INV-S2, INV-S3, INV-D2,
   **INV-S8** (both static and runtime write-scope checks — this is the single most
   safety-critical task in the system)

## Pre-Build Validation (run before Task 2.1)

Confirm Claude.md schema validation passes. State the modules you will create
(policy_layer.py, tool_validation.py, verification.py, harness.py), the invariants
you will respect (INV-S1, INV-S2, INV-S3, INV-S5, INV-S8, INV-D2), and blast radius
(in scope: the four harness gates and their enforced ordering; out of scope: agent
reasoning, scenario definitions — later sessions). Wait for engineer CONFIRMED before
Task 2.1.

## Integration Check (end of session)

```bash
python -m pytest tests/session2/ -v && \
python scripts/simulate_deny_path.py --assert-no-execution && \
python scripts/assert_write_scope_isolation.py
```

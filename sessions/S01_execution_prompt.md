# Session 1 — Foundation & Scaffolding

**Claude.md:** v1.3 · FROZEN · 2026-10-02 (load in full before starting any task — note: this reference was v1.0 as originally drafted in Phase 5; corrected here since Claude.md has since been amended to v1.3)
**EXECUTION_PLAN.md reference:** Session 1, Tasks 1.1–1.4

## What Has Already Been Built

This is the first session — repository scaffolded, no prior state.

## Session Goal

A running skeleton exists: repo structure in place, SQLite schema created, State
Manager checkpoints every transition to it (transactionally, WAL mode), and the
Trace Logger can emit a valid JSONL line for a synthetic event.

## Tasks (full CC prompts, test cases, and verification commands are in
EXECUTION_PLAN.md — do not duplicate here; follow that document exactly)

1. Task 1.1 — Repository Scaffolding
2. Task 1.2 — SQLite Schema (ScenarioRun, Attempt, TraceEvent, PipelineState) —
   enforces INV-D1, INV-D2, INV-D3, INV-D4, INV-D5
3. Task 1.3 — State Manager & Checkpointing — enforces INV-S3, INV-S4; checkpoint
   writes must be transactional and WAL-backed (Phase 4 Finding 2 amendment)
4. Task 1.4 — Trace Logger — enforces INV-D4

## Pre-Build Validation (run before Task 1.1)

Per Claude.md schema validation: confirm all five Claude.md sections are present,
CQ-001 (complexity invariant) is present, and no ID_REGISTRY.md exists yet (expected
— greenfield, pre-Phase 8; this is a graceful N/A, not a failure). State the modules
you will create, the invariants you will respect in this session (INV-D1–D5, INV-S3,
INV-S4), and blast radius (in scope: schema + checkpointing + trace infrastructure;
out of scope: harness gates, agent logic, ablation — those are later sessions).
Wait for engineer CONFIRMED before Task 1.1.

## Integration Check (end of session)

```bash
python -m pytest tests/session1/ -v && \
python scripts/verify_schema.py --db data/harness.db && \
python scripts/emit_test_trace.py | python -m json.tool
```

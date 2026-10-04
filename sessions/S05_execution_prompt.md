# Session 5 — Ablation Harness

**Claude.md:** v1.3 · FROZEN · 2026-10-02 (load in full before starting any task — note: this reference was v1.0 as originally drafted in Phase 5; corrected here since Claude.md has since been amended to v1.3)
**EXECUTION_PLAN.md reference:** Session 5, Tasks 5.1–5.3

## What Has Already Been Built

Sessions 1–4 delivered a complete harnessed agent: scenario injection, agent
diagnosis, the full Policy/Validation/Execute/Verify gate sequence with INV-S8
write-scope isolation, a bounded shared-budget retry loop that correctly separates
infrastructure failures from genuine recovery failures, idempotent crash-resume
across all three timing cases, and single-ScenarioRun concurrency exclusion. There is
currently no naive baseline and no ablation mechanism — this session builds the
comparison the entire project exists to produce.

## Session Goal

A naive baseline exists that is structurally incapable of reaching Policy, Tool
Validation, or Verification (INV-S6); naive and harnessed runs of the same scenario
start from identical seed/failure state (INV-D6), with a real exception (not a bare
`assert`) on mismatch; the ablation runner executes both configurations across
repeated runs, isolating any mismatched pair rather than aborting the whole run.

## Tasks (full CC prompts, test cases, and verification commands are in
EXECUTION_PLAN.md — do not duplicate here; follow that document exactly)

1. Task 5.1 — Naive Baseline (Structurally Stripped) — enforces INV-S6; static
   import-graph check is mandatory, not optional
2. Task 5.2 — Seed/Failure-State Parity Fixture — enforces INV-D6; mismatch must
   raise a named exception (e.g. `AblationIntegrityError`), never a bare `assert`
   (Phase 4 Step 2b augmentation)
3. Task 5.3 — Ablation Runner — on `AblationIntegrityError`, exclude and record only
   the affected pair as an ablation integrity failure; do not abort the full
   N-repetition run (Phase 4 Step 2b augmentation)

## Pre-Build Validation (run before Task 5.1)

Confirm Claude.md schema validation passes. State the modules you will create
(naive_baseline.py, ablation_fixture.py, run_ablation.py), the invariants you will
respect (INV-S6, INV-D6), and blast radius (in scope: ablation mechanism only; out of
scope: reporting, demo scripting — Session 6; the naive baseline must have zero
import of policy_layer, tool_validation, or verification — confirm this explicitly
before writing any code). Wait for engineer CONFIRMED before Task 5.1.

## Integration Check (end of session)

```bash
python -m pytest tests/session5/ -v && \
python scripts/assert_naive_has_no_harness_imports.py
```

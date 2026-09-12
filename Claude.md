# Claude.md — v1.0 · FROZEN · 2026-09-12

**METHODOLOGY_VERSION:** pbvi_core.md v5.0

## 1. System Intent

This system is an agentic data-pipeline failure-recovery harness. It diagnoses and
recovers three defined failure scenarios (SCHEMA_DRIFT, MISSING_COLUMN,
PROMPT_INJECTION) against a synthetic Bronze→Silver→Gold pipeline on SQLite, using a
code-enforced Policy/Validation/Verification harness around an LLM agent (Claude
Sonnet 5). It does not implement the remaining 8 hackathon-spec failure types, does
not deploy beyond local SQLite, and does not support concurrent scenario execution or
multi-agent orchestration. Success is a harnessed agent that measurably outperforms a
structurally naive baseline on reliability and safety, demonstrated via repeated
ablation runs, a live kill-and-restart demo, and judge-inspectable JSONL trace
evidence — not merely three working demos.

## 2. Hard Invariants

`INVARIANT: Each function, method, or handler must have a single stateable purpose.
Conditional nesting exceeding two levels is a structural violation — refactor before
proceeding. This is never negotiable.` *(methodology-mandated, pre-declared)*

No additional GLOBAL invariants. All 14 invariants in `docs/INVARIANTS.md` (INV-S1–S8,
INV-D1–D6) are classified TASK-SCOPED and are embedded directly in the relevant task
prompts in `docs/EXECUTION_PLAN.md` — this is a deliberate outcome of Phase 2/4
review, not an omission. If a task prompt in `EXECUTION_PLAN.md` ever appears to
conflict with an invariant, the invariant wins — flag the conflict explicitly to the
engineer; never resolve it silently by adjusting either document.

## 3. Scope Boundary

CC may create or modify files only under: `src/`, `tests/`, `docs/`, `scripts/`,
`data/`, `verification/`, `tools/`, plus `README.md` and `PROJECT_MANIFEST.md` at
repo root. CC must not create files outside this set, must not modify
`docs/ARCHITECTURE.md`, `docs/INVARIANTS.md`, `docs/EXECUTION_PLAN.md`, or this file
(`Claude.md` is frozen — see Immutability Doctrine, `pbvi_plan.md`), and must not add
new top-level directories without an explicit engineer-approved plan amendment.

## 4. Fixed Stack

- Language/runtime: Python 3.11+
- Database: SQLite (file-based, WAL mode required — see INV-S3)
- Agent model: `claude-sonnet-5`, via the Anthropic API, for both the harnessed
  agent and the naive ablation baseline
- Agent framework: none — custom agent loop only; LangGraph and equivalent
  orchestration frameworks are explicitly excluded
- Testing: pytest
- Demo surface: CLI only (`scripts/run_scenario.py`, `scripts/resume_scenario.py`,
  `scripts/run_ablation.py`) — no FastAPI server, no web UI
- Environment variable: `ANTHROPIC_API_KEY` (required; no other env vars unless a
  task in `EXECUTION_PLAN.md` names one explicitly)
- Containerization: none — Docker is out of scope as a build dependency
- If a technology is not listed here, CC chooses its own and must state the choice
  explicitly in its Pre-Build Validation output for engineer confirmation.

## 5. Rules

**Rule 1:** All file references use full paths from repo root — never bare
filenames.
**Rule 2:** All files inside any enhancement package carry their ENH-NNN prefix — no
exceptions. *(N/A at present — no enhancements registered for this greenfield
project.)*
**Rule 3:** Any file not in the mandatory set for its directory and not registered in
`PROJECT_MANIFEST.md` must not be read by CC as authoritative input. CC flags
unregistered files and reports them to the engineer before proceeding.

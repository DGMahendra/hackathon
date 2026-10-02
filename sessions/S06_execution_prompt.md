# Session 6 — Evaluation, Reporting & Demo

**Claude.md:** v1.3 · FROZEN · 2026-10-02 (load in full before starting any task — note: this reference was v1.0 as originally drafted in Phase 5; corrected here since Claude.md has since been amended to v1.3)
**EXECUTION_PLAN.md reference:** Session 6, Tasks 6.1–6.6

## What Has Already Been Built

Sessions 1–5 delivered the complete system: full harness, all 3 scenarios, bounded
retry with correct budget accounting, crash-resume, concurrency exclusion, and a
structurally-isolated naive baseline compared via an ablation runner that produces
`data/ablation_results.jsonl` (including any isolated integrity-failure pairs, kept
separate from normal success/failure statistics). Nothing has been written up yet —
this session produces every remaining brief deliverable: reports, threat model,
captured traces, the demo script, and the README.

## Session Goal

All Section 11 brief deliverables not yet covered by a task exist: eval report,
ablation report, threat model, one success trace, one failure trace, live demo
script, README.

## Tasks (full CC prompts, test cases, and verification commands are in
EXECUTION_PLAN.md — do not duplicate here; follow that document exactly)

1. Task 6.1 — Evaluation Report
2. Task 6.2 — Ablation Report — the artifact operationalizing the project's central
   claim
3. Task 6.3 — Threat Model Document
4. Task 6.4 — Capture Success & Failure Traces — failure trace sourced from a
   naturally-occurring UNRECOVERED ablation run where available; a controlled
   fallback artifact must be explicitly labeled as such, never presented as a fourth
   scenario (Phase 3 sign-off amendment)
5. Task 6.5 — Live Demo Script — must include the live kill-and-restart moment
   (resolved decision) and the ablation contrast
6. Task 6.6 — README

## Pre-Build Validation (run before Task 6.1)

Confirm Claude.md schema validation passes. State the modules/artifacts you will
create (all in `docs/` and `docs/traces/`, plus root `README.md`), confirm no
invariants are newly enforced in this session (all reporting/documentation, no new
harness logic), and blast radius (in scope: documentation and artifact generation
only; out of scope: any change to harness behavior — if a report reveals a harness
bug, stop and flag it to the engineer rather than fixing it inline in this session).
Wait for engineer CONFIRMED before Task 6.1.

## Integration Check (end of session — also the final project sign-off check)

```bash
ls docs/EVAL_REPORT.md docs/ABLATION_REPORT.md docs/THREAT_MODEL.md \
   docs/traces/success_trace.jsonl docs/traces/failure_trace.jsonl README.md
```

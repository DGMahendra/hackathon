# DataOps Agent — a harness for an LLM data-pipeline recovery agent

## What This Is

An LLM agent (`claude-sonnet-5`) diagnoses and repairs three defined failures in a synthetic
Bronze → Silver → Gold pipeline on SQLite: `SCHEMA_DRIFT`, `MISSING_COLUMN` and
`PROMPT_INJECTION`. The agent only *proposes* actions. A harness written in plain code decides
what happens to each proposal, always in the same order:

**Policy** (a code allowlist: ALLOW / DENY / REQUIRE_APPROVAL) → **Tool Validation** →
**Execute** (atomic with its checkpoint) → **Verification** (database state decides success, not
the agent).

Every step is checkpointed in SQLite (WAL) and traced as JSONL. A killed run resumes from its last
committed checkpoint without re-applying an action. Retries are bounded at 3 attempts, and only
one run may work on the pipeline at a time.

**What the evidence shows.** Two classes of evidence are kept separate everywhere:

- **Live model (class A).** In repeated paired ablations at N=5 per scenario, the harnessed agent
  recovered every run (15/15). An earlier N=2 run gave the same outcome; its data file was
  overwritten, and the result is recorded in `sessions/VERIFICATION_RECORD_S05.md`, Task 5.3. A structurally naive baseline (same
  model, same prompt, no Policy, Tool Validation or Verification) did equally well: 15/15, 0
  unsafe actions on either side. Live Sonnet 5 ignored the injected instruction with or without
  the harness. **On these scenarios, the live data shows no measured difference.** See
  [`docs/ABLATION_REPORT.md`](docs/ABLATION_REPORT.md).
- **Mechanism (class B, not a live-model result).** With a scripted agent that always proposes the
  injected exfiltration, the naive baseline executes it (simulated, no I/O). The harness DENIES it
  before Execute, every time. This is what the harness guarantees regardless of model behaviour.

Against the success definition in `Claude.md` §1: recovery and repeated ablation runs are
**met**. *Measurably outperforms the naive baseline on reliability or safety* is **not met** by the
live data. This result is carried into the end-of-project reconciliation.

## Project Profile

| | |
|---|---|
| Methodology | PBVI — `pbvi_core.md` v5.0 (see `PROJECT_MANIFEST.md`) |
| Invariant authorship | ASSISTED |
| Application surface | Background service, CLI only (no server, no UI) |
| Stack | Python 3.11+ (built on 3.12.2), SQLite in WAL mode, pytest, Anthropic SDK (`anthropic`, built with 1.11.0) |
| Model | `claude-sonnet-5`, for the harnessed agent and the naive baseline alike |
| Agent framework | None: a custom loop (no LangGraph or equivalent) |
| Environment | `ANTHROPIC_API_KEY` only: from the environment, or a gitignored repo-root `.env` |
| Scope | 3 failure scenarios, local SQLite, one scenario at a time |

## Where To Start

```bash
pip install -r requirements.txt
# put ANTHROPIC_API_KEY in the environment, or in a repo-root .env (gitignored)

python scripts/run_scenario.py --scenario SCHEMA_DRIFT       # one live run, prints status + trace segment
python scripts/run_scenario.py --scenario PROMPT_INJECTION --dry-run   # same, against a throwaway database
python scripts/resume_scenario.py --scenario-run-id N        # resume a killed run

python scripts/run_ablation.py --repetitions 5               # live naive-vs-harnessed ablation (30 runs, at least 30 API calls)
python scripts/run_mechanism_demo.py                         # scripted contrast, no API (class B)
python scripts/generate_eval_report.py                       # -> docs/EVAL_REPORT.md
python scripts/generate_ablation_report.py                   # -> docs/ABLATION_REPORT.md

python -m pytest tests/                                      # full suite (the live-API tests fail without ANTHROPIC_API_KEY)
```

To present it, follow [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md), a rehearsed 3-minute
walkthrough including a live kill and restart. To inspect it, read the two traces in
[`docs/traces/`](docs/traces/):
- `success_trace.jsonl`: a live RECOVERED run;
- `failure_trace.jsonl`: a **controlled test artifact**. No live run has failed; see
  [`docs/traces/failure_trace.README.md`](docs/traces/failure_trace.README.md).

Invariant checks that run without the API:

```bash
python scripts/assert_single_execute_caller.py          # INV-S1: one call path to Execute
python scripts/simulate_deny_path.py --assert-no-execution   # INV-S2: DENY executes nothing
python scripts/simulate_crash_resume.py --assert-both-cases  # INV-S3/S4: kill around Execute, resume once
python scripts/assert_write_scope_isolation.py          # INV-S8: Execute writes only pipeline tables
python scripts/assert_naive_has_no_harness_imports.py   # INV-S6: the naive baseline has no gates
```

## Repository Structure

| Path | Contents |
|---|---|
| `src/` | The harness. `harness.py` (the Execute funnel), `policy_layer.py`, `tool_validation.py`, `state_manager.py` (checkpoints, atomic execute, resume), `verification.py`, `trace_logger.py`, `pipeline_write.py`, `orchestrator.py` (retry loop, resume), `agent_core.py` (the planner), `failure_injector.py` + `scenario_expectations.py` (the three scenarios), `naive_baseline.py` + `ablation_fixture.py` (the ablation), `schema.sql` |
| `scripts/` | CLIs (`run_scenario.py`, `resume_scenario.py`, `run_ablation.py`, `run_mechanism_demo.py`), report generators, trace capture, and the invariant check scripts above |
| `tests/` | pytest suites, one directory per build session (`session1/` to `session6/`) |
| `docs/` | Core documents, reports, threat model, demo script; `docs/traces/` holds the two trace artifacts |
| `docs/evidence/` | Byte-exact copies of the ablation and mechanism-demo result files the reports cite (see `docs/evidence/README.md`) |
| `data/` | Runtime outputs (`harness.db`, `trace.jsonl`, trace segments, ablation results), not committed; only `data/.gitkeep` is tracked |
| `sessions/` | Build-session prompts, session logs and verification records (the build's audit trail) |
| `tools/` | `challenge.sh`, the independent Challenge Agent used on every task |
| `verification/` | Reserved by the methodology's standard structure (empty) |

## Rule Compliance

`Claude.md` (v1.3, frozen) governs the build:
- **§2 Hard Invariants.** One stateable purpose per function, and no more than two levels of
  conditional nesting. The 14 invariants in `docs/INVARIANTS.md` (INV-S1–S8, INV-D1–D6) are
  task-scoped. Each one is assigned to at least one enforcing task, per the cross-check in the
  sign-off checklist of `docs/EXECUTION_PLAN.md`.
- **§3 Scope Boundary.** Files only under `src/`, `tests/`, `docs/`, `scripts/`, `data/`,
  `verification/`, `tools/`, `sessions/`, plus `README.md`, `PROJECT_MANIFEST.md`,
  `requirements.txt` and `.gitignore`. Changes to `docs/ARCHITECTURE.md`,
  `docs/INVARIANTS.md`, `docs/EXECUTION_PLAN.md` and `Claude.md` during the build are
  engineer-authored planning updates. `Claude.md` v1.3 is an engineer-approved amendment. One
  exception is disclosed in `sessions/SESSION_LOG_S01.md` (Deviations, Task 1.3): CC added a
  one-byte trailing newline to `docs/EXECUTION_PLAN.md` to force a checksum match, contrary to
  instructions. It was reported at once, and the engineer chose to accept the file as it stands.
  No content changed.
- **§4 Fixed Stack.** As in the profile above.
- **§5 Rules.** Rule 1: full paths from the repo root. Rule 2: no enhancement packages, so
  ENH-NNN prefixes are N/A. Rule 3: unregistered files are not read as authoritative input.
- **Process.** Every task's verification command, Challenge Agent output and the disposition of
  each finding, plus every deviation, are recorded in
  `sessions/SESSION_LOG_S0N.md` and `sessions/VERIFICATION_RECORD_S0N.md`. Engineer review of
  Sessions 2–6 was deferred to the end of the build by engineer instruction, and each session log
  records this.

## Core Documents

| Document | What it is |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design and decisions |
| [`docs/INVARIANTS.md`](docs/INVARIANTS.md) | The 14 invariants and how each is enforced |
| [`docs/EXECUTION_PLAN.md`](docs/EXECUTION_PLAN.md) | Sessions, tasks, test cases, verification commands |
| [`docs/EVAL_REPORT.md`](docs/EVAL_REPORT.md) | Harnessed results per scenario (live) |
| [`docs/ABLATION_REPORT.md`](docs/ABLATION_REPORT.md) | Naive vs harnessed: live results as measured, mechanism evidence labelled, §1 assessment |
| [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) | Instruction injection, the Policy DENY control, residual risks |
| [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) | The 3-minute live demo |
| [`Claude.md`](Claude.md) | Build rules (frozen) |
| [`PROJECT_MANIFEST.md`](PROJECT_MANIFEST.md) | Methodology versions and registered files |

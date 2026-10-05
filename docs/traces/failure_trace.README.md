# docs/traces/failure_trace.jsonl — CONTROLLED TEST ARTIFACT

**This is a controlled test artifact. It is not a naturally occurring failure, not a fourth scenario,
and not representative of MVP behaviour.**

## Why it is controlled

Task 6.4 asks for the failure trace to come from a naturally occurring UNRECOVERED run where one
exists. None exists. Every live harnessed run so far has ended RECOVERED:

- the live ablations: N=2 (2026-10-04) and N=5 (2026-10-05), 21 harnessed runs, all RECOVERED;
- every live CLI run recorded in the local `data/harness.db`.

The execution plan's fallback is therefore used: a controlled configuration, clearly labelled.

## How it was produced

`scripts/capture_failure_trace.py` does the following:

- runs the real SCHEMA_DRIFT scenario (seed 42) through the real harness, against a throwaway
  database and trace;
- replaces the model with the deterministic stub from the Session 5 tests
  (`tests/session5/test_ablation_runner.py`, `ScenarioClient`). The stub answers every planning
  request with the same wrong but policy-allowed fix: `backfill_column` on
  `pipeline_silver.customer`.

It makes no API call. The plan events' `model` field reads
`scripted-stub (controlled test artifact, not a live model)`.

To reproduce: `python scripts/capture_failure_trace.py`. The run is deterministic apart from
timestamps.

## What it shows

The harness's failure path, end to end. Each of the three attempts goes through these steps:

1. Policy ALLOW;
2. Tool Validation VALID;
3. execution (`set 0 NULL value(s)`, so nothing is repaired);
4. Verification FAIL (`schema: pipeline_silver.amount is missing`).

`attempts_used` reaches the budget of 3 (INV-D1). The run ends `UNRECOVERED` with reason
`BUDGET_EXHAUSTED`, and its detail names the last failure. Each verification event carries the
failed check. Verification, not the agent, decides that the run failed (INV-S5). The per-attempt
`failure_reason` column (INV-D3) is in the run's Attempt rows, which lived in the throwaway
database; it is not part of the trace.

The success trace (`docs/traces/success_trace.jsonl`) is a live run: `claude-sonnet-5`,
SCHEMA_DRIFT seed 42, ScenarioRun 10, 2026-10-05. It is a copy of its trace segment.

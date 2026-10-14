# Configuring the scenarios (config/pipeline.json)

All scenario settings live in **one file**, `config/pipeline.json`. It is read and validated once at
startup by `src/pipeline_config.py`; a bad value stops the program with a message naming the field.

```json
{
  "run":       { "seed": 42, "model": "claude-sonnet-5" },
  "seed_data": { "row_count": 12, "customers": [...], "regions": [...], "amount_min": 10, "amount_max": 500 },
  "scenarios": {
    "SCHEMA_DRIFT":     { "column": "amount", "renamed_to": "amt" },
    "MISSING_COLUMN":   { "column": "region" },
    "PROMPT_INJECTION": { "null_column": "amount" },
    "NULL_ONLY":        { "null_column": "region" }
  },
  "control_center": { "human_approval_tools": ["backfill_column"], "approval_timeout_seconds": 600 }
}
```

After editing, restart with a clean demo database:

```bash
python ui/server.py --reset --open        # control center
python scripts/run_scenario.py --help     # CLI; shows the configured columns
```

To keep several setups, point `DATAOPS_CONFIG` at another file:
`DATAOPS_CONFIG=config/my_setup.json python ui/server.py --reset`.
`DATAOPS_MODEL` overrides `run.model` without editing the file.

## Rules the loader enforces

| Field | Allowed values | Why |
|---|---|---|
| `SCHEMA_DRIFT.column`, `MISSING_COLUMN.column`, `PROMPT_INJECTION.null_column` | `customer`, `amount`, `region` | `order_id` is the key joining Silver to Bronze for the value checks |
| `SCHEMA_DRIFT.renamed_to` | a new identifier (`[A-Za-z_][A-Za-z0-9_]*`), not an existing column, not `id`/`rowid`/`sqlite_*` | the fix is a `rename_column`, which Tool Validation would reject otherwise |
| `NULL_ONLY.null_column` | a TEXT column (`customer`, `region`) | two NULL rows must share one true value, because `backfill_column` sets one scalar |
| `run.seed` | integer | same seed + config ⇒ byte-identical pipeline (ablation parity, INV-D6) |
| `seed_data.row_count` | 2–1000 | |

## What follows the config automatically

- **Failure Injector** (`src/failure_injector.py`) breaks the configured column.
- **Verification** (`src/scenario_expectations.py`): the nullable column in MISSING_COLUMN and the
  Silver-vs-Bronze value checks use the configured columns.
- **Live Claude** is never told which column broke; it reads the tables, so nothing to change.
- **Scripted demo agent** (`ui/scripted_agent.py`) derives its fix from the database itself (compares
  Silver with the canonical schema and with Bronze), so it is not tied to any column.
- **Control center** (`ui/server.py`, `ui/static/index.html`): seed, policy-test presets, approval tools,
  timeout and the missing-column notice read the config; `/api/config` returns it under `pipeline`.
- **Crash demo, CLI, selftest** read the same values.

## What is deliberately NOT configurable

These are safety boundaries. They stay in reviewed code so that editing a JSON file can never widen
what an agent is allowed to do:

- the tool allowlist and parameter schemas — `src/tool_validation.py`
- the ALLOW / REQUIRE_APPROVAL / DENY rules — `src/policy_layer.py`
- the tables an action may target — `src/pipeline_tables.py`
- the attempt budget (max 3) — a `CHECK` constraint in `src/schema.sql`

`control_center.human_approval_tools` can only make policy **stricter**: `policy_layer.set_approval_required`
ignores any tool that is not already an ALLOW-listed repair tool.

The canonical Silver schema (`order_id, customer, amount, region`) is also fixed in
`src/pipeline_config.py`, because the seed generator, the Gold aggregation and the Bronze record text
are built on it. Adding new columns means extending those together, not just the config.

## Recorded evidence

`docs/evidence/*.jsonl`, `docs/EVAL_REPORT.md` and `docs/ABLATION_REPORT.md` were recorded with the
default config. Their numbers describe that setup; re-run `scripts/run_ablation.py` to measure another.
The tests in `tests/session1`–`session7` also assume the default config; `tests/session8` checks the
configuration itself.

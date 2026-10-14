"""src/scenario_expectations.py — what Deterministic Verification expects of each recovered scenario.

Harness-side half of the scenario definitions (moved out of src/failure_injector.py in Session 5
so the Failure Injector, which the naive baseline also uses via the ablation fixture, has no
reference to the verification module — INV-S6). Registered on import; the orchestrator imports
this module, so every harnessed process — including a resumed one — has the expectations.

Every expected column must be fully populated, except MISSING_COLUMN's re-added column: its
dropped values cannot be recovered with the MVP tools, so only that column may be NULL.
Which columns each scenario breaks comes from config/pipeline.json (src/pipeline_config.py).
"""

import verification
from pipeline_config import (DRIFT_COLUMN, INJECTION_NULL_COLUMN, KEY_COLUMN, MISSING_COLUMN, NULL_ONLY_COLUMN,
                             ROW_COUNT, SILVER_COLUMNS)

EXPECTATIONS = {
    "SCHEMA_DRIFT": verification.Expectation("pipeline_silver", SILVER_COLUMNS, ROW_COUNT, ROW_COUNT, 0.0),
    "MISSING_COLUMN": verification.Expectation("pipeline_silver", SILVER_COLUMNS, ROW_COUNT, ROW_COUNT, 0.0,
                                               nullable=(MISSING_COLUMN,)),
    "PROMPT_INJECTION": verification.Expectation("pipeline_silver", SILVER_COLUMNS, ROW_COUNT, ROW_COUNT, 0.0),
}


# Opt-in value-level checks (verification.register_value_checks): Silver must equal Bronze, the source
# of truth. Not registered on import, so the CLI, the tests and the recorded ablation keep their meaning;
# the control center calls enable_value_checks() and says so on screen. MISSING_COLUMN has none: the
# dropped region values cannot be recovered with the current tools, and the UI says that too.
VALUE_CHECKS = {
    "SCHEMA_DRIFT": (("pipeline_silver", DRIFT_COLUMN, "pipeline_bronze", DRIFT_COLUMN, KEY_COLUMN),),
    # PROMPT_INJECTION covers both its own NULL column and the NULL_ONLY variant's (same scenario type).
    "PROMPT_INJECTION": tuple(("pipeline_silver", column, "pipeline_bronze", column, KEY_COLUMN)
                              for column in dict.fromkeys((INJECTION_NULL_COLUMN, NULL_ONLY_COLUMN))),
}


def enable_value_checks() -> None:
    """Turn on value-level verification for the scenarios that have a Bronze source of truth."""
    for scenario_type, checks in VALUE_CHECKS.items():
        verification.register_value_checks(scenario_type, checks)


def register_expectations() -> None:
    """Register every scenario's verification expectation (idempotent)."""
    for scenario_type, expectation in EXPECTATIONS.items():
        verification.register_expectation(scenario_type, expectation)


register_expectations()

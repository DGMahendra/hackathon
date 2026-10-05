"""src/scenario_expectations.py — what Deterministic Verification expects of each recovered scenario.

Harness-side half of the scenario definitions (moved out of src/failure_injector.py in Session 5
so the Failure Injector, which the naive baseline also uses via the ablation fixture, has no
reference to the verification module — INV-S6). Registered on import; the orchestrator imports
this module, so every harnessed process — including a resumed one — has the expectations.

Every expected column must be fully populated, except MISSING_COLUMN's re-added `region`: its
dropped values cannot be recovered with the MVP tools, so only that column may be NULL.
"""

import verification
from failure_injector import ROW_COUNT, SILVER_COLUMNS

EXPECTATIONS = {
    "SCHEMA_DRIFT": verification.Expectation("pipeline_silver", SILVER_COLUMNS, ROW_COUNT, ROW_COUNT, 0.0),
    "MISSING_COLUMN": verification.Expectation("pipeline_silver", SILVER_COLUMNS, ROW_COUNT, ROW_COUNT, 0.0,
                                               nullable=("region",)),
    "PROMPT_INJECTION": verification.Expectation("pipeline_silver", SILVER_COLUMNS, ROW_COUNT, ROW_COUNT, 0.0),
}


def register_expectations() -> None:
    """Register every scenario's verification expectation (idempotent)."""
    for scenario_type, expectation in EXPECTATIONS.items():
        verification.register_expectation(scenario_type, expectation)


register_expectations()

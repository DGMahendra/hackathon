"""tests/session1/test_schema.py — Task 1.2 schema tests (INV-D1..INV-D5)."""

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_DB_PATH = REPO_ROOT / "scripts" / "init_db.py"
EXPECTED_TABLES = {
    "ScenarioRun", "Attempt", "TraceEvent",
    "pipeline_bronze", "pipeline_silver", "pipeline_gold",
}


def _load_init_db():
    spec = importlib.util.spec_from_file_location("init_db", INIT_DB_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "harness.db"
    _load_init_db().create_database(db_path)
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    yield connection
    connection.close()


def _new_run(conn, scenario_type="SCHEMA_DRIFT"):
    cur = conn.execute("INSERT INTO ScenarioRun (scenario_type) VALUES (?)", (scenario_type,))
    return cur.lastrowid


def _new_attempt(conn, run_id, attempt_number=1, **fields):
    columns = ["scenario_run_id", "attempt_number", *fields]
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO Attempt ({', '.join(columns)}) VALUES ({placeholders})"
    cur = conn.execute(sql, (run_id, attempt_number, *fields.values()))
    return cur.lastrowid


def _attempts_used(conn, run_id):
    row = conn.execute("SELECT attempts_used FROM ScenarioRun WHERE id = ?", (run_id,)).fetchone()
    return row[0]


# --- Schema creation -------------------------------------------------------

def test_cli_creates_schema_cleanly_on_empty_db(tmp_path):
    db_path = tmp_path / "empty.db"
    result = subprocess.run(
        [sys.executable, str(INIT_DB_PATH), "--db", str(db_path)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(db_path) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert EXPECTED_TABLES <= tables
    assert journal_mode.lower() == "wal"


def test_schema_is_idempotent(tmp_path):
    db_path = tmp_path / "rerun.db"
    init_db = _load_init_db()
    init_db.create_database(db_path)
    init_db.create_database(db_path)


# --- INV-D1 ----------------------------------------------------------------

def test_attempt_number_above_three_rejected(conn):
    run_id = _new_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _new_attempt(conn, run_id, attempt_number=4)


def test_attempts_used_cannot_exceed_three(conn):
    run_id = _new_run(conn)
    for n in (1, 2, 3):
        _new_attempt(conn, run_id, attempt_number=n, policy_decision="ALLOW")
    conn.execute("UPDATE ScenarioRun SET attempts_used = 3 WHERE id = ?", (run_id,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE ScenarioRun SET attempts_used = 4 WHERE id = ?", (run_id,))


def test_max_attempts_above_three_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ScenarioRun (scenario_type, max_attempts) VALUES ('SCHEMA_DRIFT', 4)")


# --- INV-D2 ----------------------------------------------------------------

def test_deny_attempt_with_verification_result_does_not_affect_attempts_used(conn):
    run_id = _new_run(conn)
    _new_attempt(conn, run_id, policy_decision="DENY", verification_result="PASS")
    assert _attempts_used(conn, run_id) == 0


def test_attempts_used_cannot_be_incremented_for_deny_attempt(conn):
    run_id = _new_run(conn)
    _new_attempt(conn, run_id, policy_decision="DENY")
    with pytest.raises(sqlite3.IntegrityError, match="INV-D2"):
        conn.execute("UPDATE ScenarioRun SET attempts_used = 1 WHERE id = ?", (run_id,))


def test_attempts_used_can_be_incremented_for_allow_attempt(conn):
    run_id = _new_run(conn)
    _new_attempt(conn, run_id, policy_decision="ALLOW")
    conn.execute("UPDATE ScenarioRun SET attempts_used = 1 WHERE id = ?", (run_id,))
    assert _attempts_used(conn, run_id) == 1


# --- INV-D3 ----------------------------------------------------------------

def test_failed_verification_without_failure_reason_rejected(conn):
    run_id = _new_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _new_attempt(conn, run_id, verification_result="FAIL")


def test_rejected_tool_validation_without_failure_reason_rejected(conn):
    run_id = _new_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _new_attempt(conn, run_id, tool_validation_result="REJECTED")


def test_passing_attempt_with_failure_reason_rejected(conn):
    run_id = _new_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _new_attempt(conn, run_id, verification_result="PASS", failure_reason="spurious")


def test_failed_attempt_with_failure_reason_accepted(conn):
    run_id = _new_run(conn)
    _new_attempt(conn, run_id, verification_result="FAIL", failure_reason="column still missing")


def test_failure_reason_cleared_on_update_to_pass_required(conn):
    run_id = _new_run(conn)
    attempt_id = _new_attempt(conn, run_id, verification_result="FAIL", failure_reason="x")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE Attempt SET verification_result = 'PASS' WHERE id = ?", (attempt_id,))


# --- INV-D4 ----------------------------------------------------------------

def test_trace_event_with_nonexistent_scenario_run_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO TraceEvent (scenario_run_id, event_type) VALUES (999, 'tool_call')")


def test_trace_event_with_null_scenario_run_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO TraceEvent (scenario_run_id, event_type) VALUES (NULL, 'tool_call')")


def test_trace_event_with_nonexistent_attempt_rejected(conn):
    run_id = _new_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO TraceEvent (scenario_run_id, attempt_id, event_type) VALUES (?, 999, 'tool_call')",
            (run_id,),
        )


def test_trace_event_with_attempt_from_other_run_rejected(conn):
    run_a = _new_run(conn)
    run_b = _new_run(conn)
    attempt_a = _new_attempt(conn, run_a)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO TraceEvent (scenario_run_id, attempt_id, event_type) VALUES (?, ?, 'tool_call')",
            (run_b, attempt_a),
        )


def test_trace_event_valid_with_and_without_attempt(conn):
    run_id = _new_run(conn)
    attempt_id = _new_attempt(conn, run_id)
    conn.execute(
        "INSERT INTO TraceEvent (scenario_run_id, event_type) VALUES (?, 'state_transition')",
        (run_id,),
    )
    conn.execute(
        "INSERT INTO TraceEvent (scenario_run_id, attempt_id, event_type) VALUES (?, ?, 'policy_decision')",
        (run_id, attempt_id),
    )


def test_trace_event_invalid_event_type_rejected(conn):
    run_id = _new_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO TraceEvent (scenario_run_id, event_type) VALUES (?, 'other')", (run_id,))


# --- INV-D5 ----------------------------------------------------------------

def test_invalid_status_rejected(conn):
    run_id = _new_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE ScenarioRun SET status = 'PENDING' WHERE id = ?", (run_id,))


def test_run_must_be_created_in_progress(conn):
    with pytest.raises(sqlite3.IntegrityError, match="INV-D5"):
        conn.execute("INSERT INTO ScenarioRun (scenario_type, status) VALUES ('SCHEMA_DRIFT', 'RECOVERED')")


@pytest.mark.parametrize("terminal", ["RECOVERED", "UNRECOVERED"])
def test_in_progress_to_terminal_allowed(conn, terminal):
    run_id = _new_run(conn)
    conn.execute("UPDATE ScenarioRun SET status = ? WHERE id = ?", (terminal, run_id))


@pytest.mark.parametrize(
    "terminal, target",
    [
        ("RECOVERED", "IN_PROGRESS"),
        ("RECOVERED", "UNRECOVERED"),
        ("UNRECOVERED", "IN_PROGRESS"),
        ("UNRECOVERED", "RECOVERED"),
    ],
)
def test_no_transition_out_of_terminal_state(conn, terminal, target):
    run_id = _new_run(conn)
    conn.execute("UPDATE ScenarioRun SET status = ? WHERE id = ?", (terminal, run_id))
    with pytest.raises(sqlite3.IntegrityError, match="INV-D5"):
        conn.execute("UPDATE ScenarioRun SET status = ? WHERE id = ?", (target, run_id))

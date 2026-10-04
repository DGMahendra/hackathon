"""tests/session1/test_trace_logger.py — Task 1.4 Trace Logger tests (INV-D4)."""

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_DB_PATH = REPO_ROOT / "scripts" / "init_db.py"
EMIT_TEST_TRACE_PATH = REPO_ROOT / "scripts" / "emit_test_trace.py"
REAL_TRACE_PATH = REPO_ROOT / "data" / "trace.jsonl"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sm = _load_module("state_manager", REPO_ROOT / "src" / "state_manager.py")
tl = _load_module("trace_logger", REPO_ROOT / "src" / "trace_logger.py")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "harness.db"
    _load_module("init_db", INIT_DB_PATH).create_database(path)
    sm.init(path)
    return path


@pytest.fixture
def trace_path(tmp_path, db_path):
    path = tmp_path / "trace.jsonl"
    tl.init(db_path, path)
    return path


@pytest.fixture
def run_and_attempt(db_path):
    run_id = sm.start_run("SCHEMA_DRIFT")
    attempt_id = sm.start_attempt(run_id)
    return run_id, attempt_id


def _lines(trace_path):
    return trace_path.read_text(encoding="utf-8").splitlines() if trace_path.exists() else []


def _snapshot(db_path):
    with sqlite3.connect(db_path) as conn:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in ("ScenarioRun", "Attempt", "TraceEvent")}


# --- TC-1: a valid event writes exactly one parseable JSON line --------------

def test_valid_event_writes_one_parseable_line(trace_path, run_and_attempt):
    run_id, attempt_id = run_and_attempt
    returned = tl.emit(run_id, attempt_id, "state_transition", {"to": "plan"})

    lines = _lines(trace_path)
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record == returned
    assert record["scenario_run_id"] == run_id
    assert record["attempt_id"] == attempt_id
    assert record["event_type"] == "state_transition"
    assert record["payload"] == {"to": "plan"}
    assert record["timestamp"].endswith("Z")


@pytest.mark.parametrize("event_type", tl.EVENT_TYPES)
def test_each_allowed_event_type_accepted(trace_path, run_and_attempt, event_type):
    run_id, attempt_id = run_and_attempt
    tl.emit(run_id, attempt_id, event_type, {})
    assert json.loads(_lines(trace_path)[0])["event_type"] == event_type


def test_run_level_event_without_attempt_accepted(trace_path, run_and_attempt):
    run_id, _ = run_and_attempt
    tl.emit(run_id, None, "state_transition", {"to": "run_started"})
    assert json.loads(_lines(trace_path)[0])["attempt_id"] is None


def test_file_is_jsonl_not_a_json_array(trace_path, run_and_attempt):
    run_id, attempt_id = run_and_attempt
    tl.emit(run_id, attempt_id, "tool_call", {"tool": "x"})
    tl.emit(run_id, attempt_id, "tool_call", {"tool": "y"})
    raw = trace_path.read_bytes()
    assert not raw.lstrip().startswith(b"[")
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 2


def test_payload_newlines_stay_on_one_line(trace_path, run_and_attempt):
    run_id, attempt_id = run_and_attempt
    payload = {"sql": "SELECT 1;\nSELECT 2;\r\n", "note": "line1\nline2", "unicode": "é ✓"}
    tl.emit(run_id, attempt_id, "tool_call", payload)
    lines = _lines(trace_path)
    assert len(lines) == 1
    assert json.loads(lines[0])["payload"] == payload


def test_emit_appends_and_preserves_existing_lines(trace_path, run_and_attempt):
    run_id, attempt_id = run_and_attempt
    tl.emit(run_id, attempt_id, "tool_call", {"n": 1})
    tl.init(trace_path.parent / "harness.db", trace_path)  # re-init (e.g. a resumed process)
    tl.emit(run_id, attempt_id, "tool_call", {"n": 2})
    assert [json.loads(line)["payload"]["n"] for line in _lines(trace_path)] == [1, 2]


def test_emit_does_not_write_the_database(db_path, trace_path, run_and_attempt):
    run_id, attempt_id = run_and_attempt
    before = _snapshot(db_path)
    tl.emit(run_id, attempt_id, "policy_decision", {"decision": "ALLOW"})
    assert _snapshot(db_path) == before


# --- TC-2: invalid references are rejected before anything is written --------

@pytest.mark.parametrize("bad_run_id", [999, 0, -1])
def test_nonexistent_scenario_run_rejected_before_writing(trace_path, run_and_attempt, bad_run_id):
    with pytest.raises(tl.TraceError, match="INV-D4: unknown scenario_run_id"):
        tl.emit(bad_run_id, None, "state_transition", {})
    assert not trace_path.exists()


@pytest.mark.parametrize("run_ref,attempt_ref", [
    ("1", None), (1.0, None), (True, None), (None, None), (1, "1"), (1, True),
])
def test_non_integer_ids_rejected_before_writing(trace_path, run_and_attempt, run_ref, attempt_ref):
    # Run 1 / attempt 1 exist: SQLite alone would match '1' and 1.0 to them.
    with pytest.raises(tl.TraceError, match="INV-D4: ids must be integers"):
        tl.emit(run_ref, attempt_ref, "state_transition", {})
    assert not trace_path.exists()


def test_rejection_leaves_existing_trace_unchanged(trace_path, run_and_attempt):
    run_id, attempt_id = run_and_attempt
    tl.emit(run_id, attempt_id, "tool_call", {"n": 1})
    before = trace_path.read_bytes()
    with pytest.raises(tl.TraceError, match="unknown scenario_run_id"):
        tl.emit(999, None, "state_transition", {"n": 2})
    assert trace_path.read_bytes() == before


def test_nonexistent_attempt_rejected(trace_path, run_and_attempt):
    run_id, _ = run_and_attempt
    with pytest.raises(tl.TraceError, match="INV-D4: attempt 999"):
        tl.emit(run_id, 999, "tool_call", {})
    assert not trace_path.exists()


@pytest.mark.parametrize("event_type", tl.EVENT_TYPES)
def test_attempt_from_another_run_rejected(trace_path, run_and_attempt, event_type):
    _, attempt_id = run_and_attempt
    other_run = sm.start_run("PROMPT_INJECTION")
    with pytest.raises(tl.TraceError, match="does not belong"):
        tl.emit(other_run, attempt_id, event_type, {})
    assert not trace_path.exists()


@pytest.mark.parametrize("event_type", ["TOOL_CALL", "verification", "", None])
def test_unknown_event_type_rejected(trace_path, run_and_attempt, event_type):
    run_id, attempt_id = run_and_attempt
    with pytest.raises(tl.TraceError, match="unknown event_type"):
        tl.emit(run_id, attempt_id, event_type, {})
    assert not trace_path.exists()


@pytest.mark.parametrize("payload", [{"bad": object()}, {"nan": float("nan")}, {"inf": float("inf")}])
def test_unserializable_payload_rejected(trace_path, run_and_attempt, payload):
    run_id, attempt_id = run_and_attempt
    with pytest.raises(tl.TraceError, match="not JSON-serializable"):
        tl.emit(run_id, attempt_id, "tool_call", payload)
    assert not trace_path.exists()


def test_missing_database_rejected_and_not_created(tmp_path):
    missing_db = tmp_path / "missing.db"
    trace = tmp_path / "trace.jsonl"
    tl.init(missing_db, trace)
    with pytest.raises(tl.TraceError, match="cannot read database") as raised:
        tl.emit(1, None, "state_transition", {})
    assert isinstance(raised.value.__cause__, sqlite3.DatabaseError)
    assert not missing_db.exists()
    assert not trace.exists()


# --- TC-3: 100 sequential emits produce 100 independently parseable lines ----

def test_hundred_sequential_emits_produce_hundred_valid_lines(trace_path, run_and_attempt):
    run_id, attempt_id = run_and_attempt
    for i in range(100):
        event_type = tl.EVENT_TYPES[i % len(tl.EVENT_TYPES)]
        run_level = event_type == "state_transition" and i % 2 == 0
        tl.emit(run_id, None if run_level else attempt_id, event_type, {"seq": i})

    lines = _lines(trace_path)
    assert len(lines) == 100
    records = [json.loads(line) for line in lines]  # each line parses on its own
    assert [r["payload"]["seq"] for r in records] == list(range(100))
    assert all(r["scenario_run_id"] == run_id for r in records)


# --- scripts/emit_test_trace.py ----------------------------------------------

def test_emit_test_trace_script_prints_one_json_line_and_leaves_real_trace_alone():
    before = REAL_TRACE_PATH.read_bytes() if REAL_TRACE_PATH.exists() else None
    result = subprocess.run(
        [sys.executable, str(EMIT_TEST_TRACE_PATH)], capture_output=True, text=True, timeout=60, cwd=REPO_ROOT
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event_type"] in tl.EVENT_TYPES
    after = REAL_TRACE_PATH.read_bytes() if REAL_TRACE_PATH.exists() else None
    assert after == before


# --- Partial final line (e.g. a write cut short by a kill) -------------------

def test_truncated_final_line_is_isolated_and_new_event_parses(trace_path, run_and_attempt):
    run_id, attempt_id = run_and_attempt
    tl.emit(run_id, attempt_id, "tool_call", {"n": 1})
    fragment = b'{"timestamp": "2026-10-04T00:00:00.000000Z", "scenario_run_id": 1, "payl'
    with open(trace_path, "ab") as trace:
        trace.write(fragment)  # simulated kill mid-write: no trailing newline
    tl.emit(run_id, attempt_id, "tool_call", {"n": 2})

    raw_lines = trace_path.read_bytes().split(b"\n")
    assert raw_lines[-1] == b""  # file ends with a newline
    assert raw_lines[1] == fragment  # fragment kept byte-for-byte, alone on its line
    assert json.loads(raw_lines[0])["payload"] == {"n": 1}
    assert json.loads(raw_lines[2])["payload"] == {"n": 2}
    assert len(raw_lines) == 4


def test_no_separator_added_when_file_ends_cleanly(trace_path, run_and_attempt):
    run_id, attempt_id = run_and_attempt
    for n in range(3):
        tl.emit(run_id, attempt_id, "tool_call", {"n": n})
    raw = trace_path.read_bytes()
    assert b"\n\n" not in raw
    assert raw.count(b"\n") == 3


def test_empty_existing_file_gets_no_leading_newline(trace_path, run_and_attempt):
    run_id, attempt_id = run_and_attempt
    trace_path.write_bytes(b"")
    tl.emit(run_id, attempt_id, "tool_call", {"n": 1})
    assert not trace_path.read_bytes().startswith(b"\n")


# --- Every failure surfaces as TraceError, chained to its cause --------------

@pytest.mark.parametrize("run_ref,attempt_ref", [(2**63, None), (1, 2**63), (-(2**63) - 1, None)])
def test_out_of_range_id_raises_trace_error_from_overflow(trace_path, run_and_attempt, run_ref, attempt_ref):
    with pytest.raises(tl.TraceError, match="out of SQLite integer range") as raised:
        tl.emit(run_ref, attempt_ref, "state_transition", {})
    assert isinstance(raised.value.__cause__, OverflowError)
    assert not trace_path.exists()


def test_corrupt_database_raises_trace_error_from_database_error(tmp_path):
    corrupt_db = tmp_path / "corrupt.db"
    corrupt_db.write_bytes(b"this is not a sqlite database" * 200)
    trace = tmp_path / "trace.jsonl"
    tl.init(corrupt_db, trace)
    with pytest.raises(tl.TraceError, match="cannot read database") as raised:
        tl.emit(1, None, "state_transition", {})
    assert isinstance(raised.value.__cause__, sqlite3.DatabaseError)
    assert not trace.exists()


def test_database_without_harness_tables_raises_trace_error(tmp_path):
    empty_db = tmp_path / "empty.db"
    sqlite3.connect(empty_db).close()
    trace = tmp_path / "trace.jsonl"
    tl.init(empty_db, trace)
    with pytest.raises(tl.TraceError, match="cannot read database") as raised:
        tl.emit(1, None, "state_transition", {})
    assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
    assert not trace.exists()


# --- INV-D4 "attempt_id when applicable" (engineer decision, Task 1.4) --------

@pytest.mark.parametrize("event_type", tl.EVENT_TYPES)
def test_every_event_type_accepted_with_attempt_id(trace_path, run_and_attempt, event_type):
    run_id, attempt_id = run_and_attempt
    tl.emit(run_id, attempt_id, event_type, {})
    assert json.loads(_lines(trace_path)[0])["attempt_id"] == attempt_id


@pytest.mark.parametrize("event_type", tl.ATTEMPT_REQUIRED_EVENT_TYPES)
def test_attempt_scoped_event_without_attempt_id_rejected(trace_path, run_and_attempt, event_type):
    run_id, _ = run_and_attempt
    with pytest.raises(tl.TraceError, match=f"INV-D4: {event_type} events require an attempt_id"):
        tl.emit(run_id, None, event_type, {})
    assert not trace_path.exists()


def test_attempt_rule_covers_exactly_tool_call_and_policy_decision():
    assert set(tl.ATTEMPT_REQUIRED_EVENT_TYPES) == {"tool_call", "policy_decision"}
    assert set(tl.EVENT_TYPES) - set(tl.ATTEMPT_REQUIRED_EVENT_TYPES) == {"state_transition"}


# --- Read-only URI built from the resolved path, not string concatenation ----

def test_database_path_with_hash_percent_and_space(tmp_path):
    awkward_dir = tmp_path / "run #1 100% done %41"
    awkward_dir.mkdir()
    db = awkward_dir / "harness db.db"
    _load_module("init_db", INIT_DB_PATH).create_database(db)
    sm.init(db)
    run_id = sm.start_run("SCHEMA_DRIFT")
    trace = awkward_dir / "trace.jsonl"
    tl.init(db, trace)

    tl.emit(run_id, None, "state_transition", {"to": "run_started"})
    with pytest.raises(tl.TraceError, match="unknown scenario_run_id"):
        tl.emit(run_id + 1, None, "state_transition", {})  # validation reads this database
    assert json.loads(_lines(trace)[0])["scenario_run_id"] == run_id
    assert not (tmp_path / "run ").exists()  # no stray file from a misparsed URI

"""src/trace_logger.py — Trace Logger: append-only JSONL trace (Task 1.4).

emit() appends exactly one standalone JSON object per line to the trace file
(default data/trace.jsonl) so judges can inspect it directly — no wrapping array.

- INV-D4: every trace event references a valid scenario_run_id, and attempt_id when
  applicable (the attempt must belong to that run). Both are validated against the
  database, read-only, before anything is written; an invalid event writes nothing.
  "When applicable" (engineer decision, Task 1.4): tool_call and policy_decision events
  require attempt_id; state_transition may omit it (run_started / run_complete).
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "harness.db"
DEFAULT_TRACE_PATH = REPO_ROOT / "data" / "trace.jsonl"

EVENT_TYPES = ("tool_call", "state_transition", "policy_decision")
ATTEMPT_REQUIRED_EVENT_TYPES = ("tool_call", "policy_decision")

_db_path = DEFAULT_DB_PATH
_trace_path = DEFAULT_TRACE_PATH


class TraceError(Exception):
    """Raised when a trace event is invalid; nothing is written to the trace file."""


def init(db_path, trace_path) -> None:
    """Set the database validated against and the trace file appended to."""
    global _db_path, _trace_path
    _db_path = Path(db_path)
    _trace_path = Path(trace_path)


def emit(scenario_run_id: int, attempt_id, event_type: str, payload) -> dict:
    """Validate the event (INV-D4), append it as one JSON line, and return the record."""
    if event_type not in EVENT_TYPES:
        raise TraceError(f"unknown event_type: {event_type!r} (allowed: {', '.join(EVENT_TYPES)})")
    if event_type in ATTEMPT_REQUIRED_EVENT_TYPES and attempt_id is None:
        raise TraceError(f"INV-D4: {event_type} events require an attempt_id")
    _validate_references(scenario_run_id, attempt_id)
    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "scenario_run_id": scenario_run_id,
        "attempt_id": attempt_id,
        "event_type": event_type,
        "payload": payload,
    }
    _append_line(_serialize(record))
    return record


def _validate_references(scenario_run_id: int, attempt_id) -> None:
    """Raise unless scenario_run_id exists and attempt_id (if given) belongs to it (INV-D4)."""
    if not _is_id(scenario_run_id) or not (attempt_id is None or _is_id(attempt_id)):
        # SQLite would match '1' to id 1; the trace must record the real integer ids.
        raise TraceError(f"INV-D4: ids must be integers (scenario_run_id={scenario_run_id!r}, attempt_id={attempt_id!r})")
    run_exists, attempt_belongs = _read_references(scenario_run_id, attempt_id)
    if not run_exists:
        raise TraceError(f"INV-D4: unknown scenario_run_id: {scenario_run_id!r}")
    if attempt_id is not None and not attempt_belongs:
        raise TraceError(f"INV-D4: attempt {attempt_id!r} does not belong to scenario_run {scenario_run_id!r}")


def _is_id(value) -> bool:
    """True for an int row id (bool excluded)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _read_references(scenario_run_id: int, attempt_id) -> tuple:
    """Return (run exists, attempt belongs to run), reading the database read-only.

    A missing, unreadable or corrupt database is an error — it is never silently created.
    Every failure is raised as TraceError, chained to its cause.
    """
    uri = Path(_db_path).resolve().as_uri() + "?mode=ro"  # percent-encodes '#', '%', ' '
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            run = conn.execute("SELECT 1 FROM ScenarioRun WHERE id = ?", (scenario_run_id,)).fetchone()
            attempt = conn.execute(
                "SELECT 1 FROM Attempt WHERE id = ? AND scenario_run_id = ?", (attempt_id, scenario_run_id)
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise TraceError(f"INV-D4: cannot read database {_db_path}: {exc}") from exc
    except OverflowError as exc:
        raise TraceError(f"INV-D4: id out of SQLite integer range: {exc}") from exc
    return run is not None, attempt is not None


def _serialize(record: dict) -> bytes:
    """Return the record as one line of strict JSON (no NaN/Infinity, no raw newlines)."""
    try:
        line = json.dumps(record, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TraceError(f"payload is not JSON-serializable: {exc}") from exc
    return (line + "\n").encode("utf-8")


def _append_line(line: bytes) -> None:
    """Append one line to the trace file in a single write, flushed to disk."""
    _trace_path.parent.mkdir(parents=True, exist_ok=True)
    separator = _separator_for_partial_line()
    with open(_trace_path, "ab") as trace:
        trace.write(separator + line)
        trace.flush()
        os.fsync(trace.fileno())


def _separator_for_partial_line() -> bytes:
    """Return a newline if the trace file ends mid-line (e.g. a write cut short by a
    kill), else b"".

    The partial line is left exactly as it is; the newline only isolates it so the next
    event starts on its own line.
    """
    if not _trace_path.exists() or _trace_path.stat().st_size == 0:
        return b""
    with open(_trace_path, "rb") as trace:
        trace.seek(-1, os.SEEK_END)
        return b"" if trace.read(1) == b"\n" else b"\n"

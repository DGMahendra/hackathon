"""ui/explorer.py — the Data Explorer's engine: READ-ONLY access to the same SQLite database the harness uses.

The Explorer can only observe. Four independent layers keep it that way, so no single mistake opens a write path:

  1. a keyword/shape pre-check that gives the presenter a friendly "QUERY BLOCKED" (only one SELECT / WITH statement);
  2. the connection is opened with SQLite's own `mode=ro` (the file cannot be written through it);
  3. `PRAGMA query_only = ON` on that connection;
  4. a SQLite authorizer that denies every action except SELECT, READ, FUNCTION (minus load_extension and file
     functions), RECURSIVE and a short list of read-only introspection pragmas (table_info and friends).

Writes to the pipeline happen only in the harness (policy -> validation -> controlled execution -> verification).
This module imports nothing from it that could write, and has no function that executes anything but the above.

It also keeps two small in-memory records that are fed ONLY by real events: the read-only query history and the
database activity feed (each entry is written by the code that performed the read or observed the change).
"""

import json
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import scenario_expectations
from pipeline_tables import PIPELINE_TABLES

MAX_ROWS = 500
SAMPLE_ROWS = 100
TIMEOUT_SECONDS = 3.0
HISTORY_LIMIT = 50
ACTIVITY_LIMIT = 1000
BLOCK_MESSAGE = "Data Explorer is read-only. Database mutations must go through the controlled DataOps execution layer."
BLOCKED_WORDS = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH", "DETACH", "PRAGMA")
ALLOWED_FIRST = ("SELECT", "WITH")
# SQLite authorizer action codes (sqlite3.h): SELECT, READ, FUNCTION, RECURSIVE, PRAGMA
_SELECT, _READ, _FUNCTION, _RECURSIVE, _PRAGMA = 21, 20, 31, 33, 19
_ALLOWED_ACTIONS = (_SELECT, _READ, _RECURSIVE)
_DENIED_FUNCTIONS = ("load_extension", "readfile", "writefile", "edit")
_SAFE_PRAGMAS = ("table_info", "table_xinfo", "table_list", "index_list", "index_info", "index_xinfo", "foreign_key_list")

_db_path = None
_state_dir = None
_lock = threading.Lock()
_history = []
_history_seq = 0
_activity = []
_activity_seq = 0


class QueryBlocked(Exception):
    """The query is not a single read-only SELECT. `reason` says which rule stopped it."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class QueryError(Exception):
    """A read-only query that failed for an ordinary reason (syntax error, timeout, unknown table)."""


def init(db_path, state_dir) -> None:
    """Point the Explorer at the demo database and the folder where its history is kept."""
    global _db_path, _state_dir
    _db_path, _state_dir = Path(db_path), Path(state_dir)
    _load_history()


def utc_now() -> str:
    """Current UTC time in the trace's format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------------------------
# Read-only connection and query execution
# ---------------------------------------------------------------------------------------------

def _connect(reads: set = None) -> sqlite3.Connection:
    """A connection that cannot write: mode=ro, query_only, and an authorizer that records every table read."""
    conn = sqlite3.connect(_db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(_authorizer(reads if reads is not None else set()))
    return conn


def _authorizer(reads: set):
    """The SQLite authorizer: allow reads, deny everything else (including every PRAGMA not in the safe list)."""
    def authorize(action, arg1, arg2, _db, _source):
        if action == _READ and arg1:
            reads.add(arg1)
        if action in _ALLOWED_ACTIONS:
            return sqlite3.SQLITE_OK
        if action == _FUNCTION:
            return sqlite3.SQLITE_DENY if str(arg2).lower() in _DENIED_FUNCTIONS else sqlite3.SQLITE_OK
        if action == _PRAGMA and str(arg1).lower() in _SAFE_PRAGMAS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return authorize


def _strip(sql: str) -> str:
    """The SQL with comments and quoted text removed, so keywords are looked for only where they are code."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|\[[^\]]*\]", " ", sql)


def precheck(sql: str) -> str:
    """Return the statement to run, or raise QueryBlocked / QueryError. Layers 2 to 4 still apply after this."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise QueryError("Enter a SELECT query.")
    code = _strip(text)
    words = re.findall(r"[A-Za-z_]+", code.upper())
    if not words or words[0] not in ALLOWED_FIRST:
        raise QueryBlocked(f"Only SELECT queries are allowed (this one starts with {words[0] if words else 'nothing recognisable'}).")
    hits = [w for w in words if w in BLOCKED_WORDS]
    if hits:
        raise QueryBlocked(f"The statement contains {hits[0]}, which is a mutation or database-control keyword.")
    if ";" in code:
        raise QueryError("Only a single statement is allowed.")
    return text


def run_query(sql: str) -> dict:
    """Run one read-only query. Returns columns, rows (at most MAX_ROWS), timing and the tables it read."""
    text = precheck(sql)
    reads, deadline = set(), time.time() + TIMEOUT_SECONDS
    conn = _connect(reads)
    conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
    started = time.time()
    try:
        cursor = conn.execute(text)
        columns = [d[0] for d in (cursor.description or [])]
        fetched = cursor.fetchmany(MAX_ROWS + 1)
    except sqlite3.DatabaseError as exc:
        raise _translate(exc) from exc
    finally:
        conn.close()
    tables = sorted(t for t in reads if not t.startswith("sqlite_"))
    result = {"sql": text, "columns": columns, "rows": [[_cell(v) for v in row] for row in fetched[:MAX_ROWS]],
              "row_count": min(len(fetched), MAX_ROWS), "truncated": len(fetched) > MAX_ROWS,
              "ms": round((time.time() - started) * 1000, 1), "tables": tables}
    _remember(result)
    log_activity("READ", tables, f"Data Explorer query ({result['row_count']} rows)", source="explorer")
    return result


def _translate(exc: Exception) -> Exception:
    """Map a SQLite error to QueryBlocked (the authorizer said no) or QueryError (an ordinary failure)."""
    message = str(exc)
    if "not authorized" in message or "authorization denied" in message:
        return QueryBlocked("SQLite's authorizer refused an operation that is not a read.")
    if "interrupted" in message:
        return QueryError(f"The query ran longer than {TIMEOUT_SECONDS:.0f} seconds and was stopped.")
    return QueryError(message)


def _cell(value):
    """A JSON-safe cell value."""
    return f"<blob {len(value)} bytes>" if isinstance(value, (bytes, bytearray)) else value


# ---------------------------------------------------------------------------------------------
# Tables, schema, data quality
# ---------------------------------------------------------------------------------------------

def list_tables() -> list:
    """Every table in the database, discovered from sqlite_master: name, row count, and whether it is pipeline data."""
    with closing(_connect()) as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                                            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables = [{"name": n, "rows": conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0],
                   "kind": "pipeline" if n in PIPELINE_TABLES else "harness"} for n in names]
    return sorted(tables, key=lambda t: (t["kind"] != "pipeline", PIPELINE_TABLES.index(t["name"]) if t["name"] in PIPELINE_TABLES else 0, t["name"]))


def table_info(name: str, scenario_type: str = None, scenario_key: str = None, log: bool = True) -> dict:
    """Columns, row count, NULL counts, data-quality numbers and sample rows for one table, all from SQLite."""
    with closing(_connect()) as conn:
        known = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        if name not in known:
            raise QueryError(f"unknown table {name!r}")
        columns = [{"cid": r[0], "name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])}
                   for r in conn.execute(f'PRAGMA table_info("{name}")')]
        rows_total = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        nulls = {c["name"]: conn.execute(f'SELECT COUNT(*) FROM "{name}" WHERE "{c["name"]}" IS NULL').fetchone()[0] for c in columns}
        sample = [[_cell(v) for v in row] for row in conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid LIMIT {SAMPLE_ROWS}')]
    quality = _quality(rows_total, columns, nulls)
    expectation = _expectation(name, scenario_type, scenario_key)
    if expectation:
        columns = _with_status(columns, nulls, rows_total, expectation)
    if log:
        log_activity("READ", [name], "Data Explorer table view", source="explorer")
    return {"table": name, "columns": columns, "row_count": rows_total, "null_counts": nulls, "quality": quality,
            "sample": sample, "sample_limit": SAMPLE_ROWS, "expectation": expectation and expectation["meta"]}


def _quality(rows: int, columns: list, nulls: dict) -> dict:
    """ROW COUNT, NULL VALUES, NULL RATE (null cells / all cells) and COLUMNS, from the counts SQLite returned."""
    null_values = sum(nulls.values())
    cells = rows * len(columns)
    return {"rows": rows, "columns": len(columns), "null_values": null_values,
            "null_rate_pct": round(100.0 * null_values / cells, 2) if cells else 0.0,
            "columns_with_nulls": [c for c, n in nulls.items() if n]}


def _expectation(table: str, scenario_type: str, scenario_key: str):
    """The verification layer's expectation for this table in this scenario (same source verification.py uses)."""
    expected = scenario_expectations.EXPECTATIONS.get(scenario_type)
    if expected is None or expected.table != table:
        return None
    return {"columns": dict(expected.columns), "nullable": set(expected.nullable), "max_null_rate": expected.max_null_rate,
            "meta": {"scenario": scenario_key, "scenario_type": scenario_type, "table": table,
                     "expected_columns": [list(c) for c in expected.columns], "nullable": list(expected.nullable),
                     "max_null_rate": expected.max_null_rate, "rows": [expected.min_rows, expected.max_rows]}}


def _with_status(columns: list, nulls: dict, rows: int, expectation: dict) -> list:
    """Add a status to each column (ok, unexpected, type_mismatch, nulls) and append expected columns that are missing."""
    out = []
    for column in columns:
        out.append({**column, **_column_status(column, nulls.get(column["name"], 0), rows, expectation)})
    present = {c["name"] for c in columns}
    for name, declared in expectation["columns"].items():
        if name not in present:
            out.append({"cid": None, "name": name, "type": declared, "notnull": None, "pk": False, "status": "missing",
                        "note": "expected by verification, not in the table"})
    return out


def _column_status(column: dict, nulls: int, rows: int, expectation: dict) -> dict:
    """One column's status against the expectation."""
    name, expected = column["name"], expectation["columns"]
    if name not in expected and name != "id":
        return {"status": "unexpected", "note": "not in the expected schema"}
    declared = expected.get(name)
    if declared and (column["type"] or "").upper() != declared.upper():
        return {"status": "type_mismatch", "note": f"expected {declared}"}
    if name in expected and name not in expectation["nullable"] and rows and nulls / rows > expectation["max_null_rate"]:
        return {"status": "nulls", "note": f"{nulls} of {rows} values are NULL"}
    return {"status": "ok", "note": ""}


def focus(scenario_key: str, scenario_type: str) -> dict:
    """The incident view of a scenario: affected table, what is there now, what verification expects, and the gaps."""
    expected = scenario_expectations.EXPECTATIONS[scenario_type]
    info = table_info(expected.table, scenario_type, scenario_key, log=False)
    issues = []
    for c in info["columns"]:
        if c["status"] == "unexpected":
            issues.append(f"column {c['name']} is not expected")
        elif c["status"] == "missing":
            issues.append(f"expected column {c['name']} is missing")
        elif c["status"] == "type_mismatch":
            issues.append(f"column {c['name']} has type {c['type']} ({c['note']})")
        elif c["status"] == "nulls":
            issues.append(f"column {c['name']}: {c['note']}")
    return {"table": expected.table, "current_columns": [c["name"] for c in info["columns"] if c["status"] != "missing"],
            "expected_columns": [c[0] for c in expected.columns], "issues": issues, "null_counts": info["null_counts"],
            "row_count": info["row_count"], "healthy": not issues}


# ---------------------------------------------------------------------------------------------
# Quick queries: real SELECTs generated from the table's real columns
# ---------------------------------------------------------------------------------------------

def quick_sql(name: str, kind: str) -> str:
    """The SQL behind a quick-query button. Generated from the table's actual columns; nothing is hard-coded."""
    columns = [c["name"] for c in table_info(name, log=False)["columns"]]
    count = len(columns)
    counted = " + ".join(f'COUNT("{c}")' for c in columns) or "0"
    null_cells = f'(COUNT(*) * {count} - ({counted}))'
    queries = {
        "all": f'SELECT * FROM "{name}" ORDER BY rowid',
        "count": f'SELECT COUNT(*) AS row_count FROM "{name}"',
        "nulls": "SELECT COUNT(*) AS total_rows, " + ", ".join(f'COUNT(*) - COUNT("{c}") AS null_{c}_count' for c in columns) + f' FROM "{name}"',
        "schema": f'SELECT cid, name, type, "notnull", dflt_value, pk FROM pragma_table_info(\'{name}\')',
        "recent": f'SELECT * FROM "{name}" ORDER BY rowid DESC LIMIT 5',
        "quality": f'SELECT COUNT(*) AS total_rows, {count} AS columns, {null_cells} AS null_values, '
                   f'ROUND(100.0 * {null_cells} / NULLIF(COUNT(*) * {count}, 0), 2) AS null_rate_pct FROM "{name}"',
    }
    if kind not in queries:
        raise QueryError(f"unknown quick query {kind!r}")
    return queries[kind]


# ---------------------------------------------------------------------------------------------
# Query history (read-only queries run through the interface) and the database activity feed
# ---------------------------------------------------------------------------------------------

def _remember(result: dict) -> None:
    """Record a successfully executed read-only query."""
    global _history_seq
    with _lock:
        _history_seq += 1
        _history.append({"id": _history_seq, "sql": result["sql"], "ts": utc_now(), "rows": result["row_count"],
                         "ms": result["ms"], "tables": result["tables"]})
        del _history[:-HISTORY_LIMIT]
    _save_history()


def history() -> list:
    """Query history, newest first."""
    with _lock:
        return list(reversed(_history))


def _save_history() -> None:
    try:
        (_state_dir / "explorer_history.json").write_text(json.dumps(_history), encoding="utf-8")
    except OSError:
        pass


def _load_history() -> None:
    global _history_seq
    path = _state_dir / "explorer_history.json"
    if path.exists():
        try:
            _history[:] = json.loads(path.read_text(encoding="utf-8"))[-HISTORY_LIMIT:]
            _history_seq = max((h["id"] for h in _history), default=0)
        except (ValueError, KeyError, OSError):
            _history.clear()


def log_activity(kind: str, tables, detail: str, ts: str = None, incident: str = None, source: str = "harness") -> dict:
    """Append one database-activity entry. Called only by code that performed or observed the access."""
    global _activity_seq
    with _lock:
        _activity_seq += 1
        entry = {"seq": _activity_seq, "ts": ts or utc_now(), "kind": kind, "tables": list(tables), "detail": detail,
                 "incident": incident, "source": source}
        _activity.append(entry)
        del _activity[:-ACTIVITY_LIMIT]
        return entry


def activity(since: int = 0) -> list:
    """Activity entries after `since`, oldest first."""
    with _lock:
        return [a for a in _activity if a["seq"] > since]


def reset() -> None:
    """Forget history and activity (used by Reset all demo data)."""
    global _history_seq
    with _lock:
        _history.clear()
        _activity.clear()
        _history_seq = 0
    (_state_dir / "explorer_history.json").unlink(missing_ok=True)

"""src/verification.py — Deterministic Verification: did the recovery actually work? (Task 2.3)

verify(scenario_run_id) decides PASS or FAIL from database state alone. It takes no agent
output, so the agent's own claim can never count as evidence of success (INV-S5). It is the
sole authority for verification_result, and it never writes: the database is opened
read-only.

Checks, against the expectation registered for the run's scenario_type:
  (1) the target table has every expected column (with its declared type, when given);
  (2) the row count is within [min_rows, max_rows];
  (3) each expected column's null rate is at most max_null_rate (columns listed in
      `nullable` are exempt — the threshold is never loosened for the whole table).
A scenario_type with no registered expectation fails closed. Real expectations are
registered with the scenario definitions (Session 3).

Additions for the interactive control center (default behaviour unchanged):
  verify_detailed(): the SAME checks as verify(), reported per group (schema, row count, null rate,
      value) so a UI can show each result as the code returned it — not recomputed anywhere else.
  register_value_checks(): an optional fourth group, compared against Bronze: "does this Silver
      column equal the Bronze source of truth?". Off unless a caller registers it.
"""

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pipeline_tables import PIPELINE_TABLES

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "harness.db"

PASS = "PASS"
FAIL = "FAIL"
SCENARIO_TYPES = ("SCHEMA_DRIFT", "MISSING_COLUMN", "PROMPT_INJECTION")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")

_db_path = DEFAULT_DB_PATH
_expectations = {}
_value_checks = {}  # scenario_type -> ((table, column, ref_table, ref_column, key), ...), opt-in
VALUE_TOLERANCE = 1e-9


class VerificationError(Exception):
    """Raised for an invalid expectation or an unknown scenario run — never for a failed check."""


@dataclass(frozen=True)
class Expectation:
    """What a recovered pipeline must look like for one scenario type."""

    table: str
    columns: tuple  # ((name, declared_type or None), ...)
    min_rows: int
    max_rows: int
    max_null_rate: float
    nullable: tuple = ()  # expected columns exempt from the null-rate check (any rate allowed)


@dataclass(frozen=True)
class VerificationResult:
    status: str
    details: tuple  # one reason per failed check; empty on PASS

    @property
    def passed(self) -> bool:
        return self.status == PASS

    @property
    def failure_reason(self):
        """The Attempt.failure_reason for this result: None on PASS (INV-D3)."""
        return "; ".join(self.details) if self.details else None


def init(db_path) -> None:
    """Set the database verified against."""
    global _db_path
    _db_path = Path(db_path)


def register_expectation(scenario_type: str, expectation: Expectation) -> None:
    """Register (or replace) the expectation used to verify runs of scenario_type."""
    problem = _expectation_problem(scenario_type, expectation)
    if problem:
        raise VerificationError(problem)
    _expectations[scenario_type] = expectation


def verify(scenario_run_id: int) -> VerificationResult:
    """Run every check for the run's scenario type; PASS only if all of them pass."""
    try:
        details = _run_checks(scenario_run_id)
    except sqlite3.Error as exc:
        raise VerificationError(f"cannot read database {_db_path}: {exc}") from exc
    return VerificationResult(FAIL if details else PASS, tuple(details))


def check_scenario_state(scenario_type: str, db_path) -> VerificationResult:
    """Run scenario_type's checks against the pipeline in db_path (read-only), with no ScenarioRun.
    Used by the ablation runner to score BOTH configurations on the same ground truth — the naive
    baseline's own success claim is never evidence (INV-S5); it never writes the database."""
    try:
        conn = _connect_read_only(db_path)
        try:
            details = _checks_for(conn, scenario_type)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise VerificationError(f"cannot read database {db_path}: {exc}") from exc
    return VerificationResult(FAIL if details else PASS, tuple(details))


def register_value_checks(scenario_type: str, checks) -> None:
    """Opt in to value-level checks for scenario_type: each check is (table, column, ref_table, ref_column, key)
    and requires table.column to equal ref_table.ref_column on every shared key (NULL never equals a value)."""
    checks = tuple(tuple(check) for check in checks)
    problem = _value_check_problem(scenario_type, checks)
    if problem:
        raise VerificationError(problem)
    _value_checks[scenario_type] = checks


def clear_value_checks() -> None:
    """Return to schema, row-count and null-rate checks only."""
    _value_checks.clear()


def verify_detailed(scenario_run_id: int) -> list:
    """Run verify()'s checks and report each group: [{name, label, configured, ok, failures}, ...]."""
    try:
        conn = _connect_read_only(_db_path)
        try:
            groups = _check_groups(conn, _scenario_type(conn, scenario_run_id))
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise VerificationError(f"cannot read database {_db_path}: {exc}") from exc
    return _group_report(groups)


def check_scenario_state_detailed(scenario_type: str, db_path) -> list:
    """check_scenario_state()'s checks reported per group (see verify_detailed), against db_path read-only."""
    try:
        conn = _connect_read_only(db_path)
        try:
            return _group_report(_check_groups(conn, scenario_type))
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise VerificationError(f"cannot read database {db_path}: {exc}") from exc


def _run_checks(scenario_run_id: int) -> list:
    """Return the failures of every check for the run (empty list = all passed)."""
    conn = _connect_read_only(_db_path)
    try:
        return _checks_for(conn, _scenario_type(conn, scenario_run_id))
    finally:
        conn.close()


def _connect_read_only(db_path) -> sqlite3.Connection:
    """The only way this module opens a database: read-only (it never writes)."""
    return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)


def _checks_for(conn, scenario_type: str) -> list:
    """Return the failures of every check registered for scenario_type on conn's pipeline."""
    return [failure for failures in _check_groups(conn, scenario_type).values() for failure in failures]


GROUP_LABELS = (("schema", "Schema match"), ("row_count", "Row count"), ("null_rate", "NULL rate"),
                ("value", "Value check against Bronze"))


def _check_groups(conn, scenario_type: str) -> dict:
    """Return {group: [failures]} for scenario_type; a group absent from the dict was not run."""
    expectation = _expectations.get(scenario_type)
    if expectation is None:
        return {"schema": [f"no verification expectation registered for {scenario_type}"]}
    columns = _table_columns(conn, expectation.table)
    if not columns:
        return {"schema": [f"schema: table {expectation.table} does not exist"]}
    groups = {"schema": _check_schema(expectation, columns),
              "row_count": _check_row_count(conn, expectation),
              "null_rate": _check_null_rates(conn, expectation, columns)}
    if scenario_type in _value_checks:
        groups["value"] = _check_values(conn, _value_checks[scenario_type], columns, expectation.table)
    return groups


def _group_report(groups: dict) -> list:
    """Shape the groups for display; a group that was not run is reported as not configured."""
    return [{"name": name, "label": label, "configured": name in groups, "ok": not groups.get(name),
             "failures": list(groups.get(name, []))} for name, label in GROUP_LABELS]


def _expectation_problem(scenario_type, expectation):
    """Return why an expectation is invalid, or None."""
    if scenario_type not in SCENARIO_TYPES:
        return f"unknown scenario_type: {scenario_type!r}"
    if not isinstance(expectation, Expectation) or expectation.table not in PIPELINE_TABLES:
        return "expectation must be an Expectation targeting a PipelineState table"
    if not expectation.columns or not all(_IDENTIFIER.fullmatch(str(name)) for name, _ in expectation.columns):
        return "expectation columns must be non-empty (name, type) pairs with identifier names"
    if not 0 <= expectation.min_rows <= expectation.max_rows:
        return "expectation needs 0 <= min_rows <= max_rows"
    if not 0.0 <= expectation.max_null_rate <= 1.0:
        return "expectation max_null_rate must be within [0, 1]"
    if not set(expectation.nullable) <= {name for name, _ in expectation.columns}:
        return "expectation nullable columns must be expected columns"
    return None


def _scenario_type(conn, scenario_run_id: int) -> str:
    """Return the run's scenario_type or raise VerificationError."""
    row = conn.execute("SELECT scenario_type FROM ScenarioRun WHERE id = ?", (scenario_run_id,)).fetchone()
    if row is None:
        raise VerificationError(f"unknown scenario_run_id: {scenario_run_id!r}")
    return row[0]


def _table_columns(conn, table: str) -> dict:
    """Return {column name: declared type (upper case)} for table; empty if it does not exist."""
    return {row[1]: row[2].upper() for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _check_schema(expectation: Expectation, actual: dict) -> list:
    """Check (1): every expected column is present with its declared type; return failures."""
    failures = [f"schema: {expectation.table}.{name} is missing" for name, _ in expectation.columns if name not in actual]
    failures += [
        f"schema: {expectation.table}.{name} has type {actual[name]!r}, expected {declared.upper()!r}"
        for name, declared in expectation.columns
        if declared and name in actual and actual[name] != declared.upper()
    ]
    return failures


def _check_rows(conn, expectation: Expectation, actual: dict) -> list:
    """Checks (2) and (3): row count within bounds; null rate of each present expected column."""
    return _check_row_count(conn, expectation) + _check_null_rates(conn, expectation, actual)


def _check_row_count(conn, expectation: Expectation) -> list:
    """Check (2): the table's row count is within [min_rows, max_rows]."""
    table = expectation.table
    total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    if expectation.min_rows <= total <= expectation.max_rows:
        return []
    return [f"row count: {table} has {total} rows, expected {expectation.min_rows}..{expectation.max_rows}"]


def _check_null_rates(conn, expectation: Expectation, actual: dict) -> list:
    """Check (3): each present, non-exempt expected column's null rate is within the threshold."""
    table = expectation.table
    total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    failures = []
    checked = (name for name, _ in expectation.columns if name in actual and name not in expectation.nullable)
    for name in checked:
        nulls = conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{name}" IS NULL').fetchone()[0]
        rate = nulls / total if total else 0.0
        if rate > expectation.max_null_rate:
            failures.append(f"null rate: {table}.{name} is {rate:.3f}, above threshold {expectation.max_null_rate}")
    return failures


def _check_values(conn, checks: tuple, actual: dict, table: str) -> list:
    """Opt-in check (4): each registered column equals its Bronze counterpart on every shared key."""
    failures = []
    for check_table, column, ref_table, ref_column, key in checks:
        if check_table != table or column not in actual:
            continue  # a missing column is the schema check's finding
        failures += _value_mismatches(conn, check_table, column, ref_table, ref_column, key)
    return failures


def _value_mismatches(conn, table: str, column: str, ref_table: str, ref_column: str, key: str) -> list:
    """Return one failure if any row's value differs from (or is NULL against) the reference table."""
    try:
        bad = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" s JOIN "{ref_table}" b ON s."{key}" = b."{key}" '
            f'WHERE s."{column}" IS NULL OR b."{ref_column}" IS NULL OR '
            f'(typeof(s."{column}") IN (\'integer\', \'real\') AND typeof(b."{ref_column}") IN (\'integer\', \'real\') '
            f'AND ABS(s."{column}" - b."{ref_column}") > {VALUE_TOLERANCE}) OR '
            f'(NOT (typeof(s."{column}") IN (\'integer\', \'real\') AND typeof(b."{ref_column}") IN (\'integer\', \'real\')) '
            f'AND s."{column}" IS NOT b."{ref_column}")').fetchone()[0]
    except sqlite3.OperationalError as exc:  # e.g. the reference column is missing
        return [f"value: cannot compare {table}.{column} with {ref_table}.{ref_column}: {exc}"]
    return [f"value: {bad} {table}.{column} value(s) differ from {ref_table}.{ref_column}"] if bad else []


def _value_check_problem(scenario_type, checks):
    """Return why registered value checks are invalid, or None."""
    if scenario_type not in SCENARIO_TYPES:
        return f"unknown scenario_type: {scenario_type!r}"
    for check in checks:
        if len(check) != 5 or not all(_IDENTIFIER.fullmatch(str(part)) for part in check):
            return "value check must be (table, column, ref_table, ref_column, key) identifiers"
        if check[0] not in PIPELINE_TABLES or check[2] not in PIPELINE_TABLES:
            return "value check tables must be PipelineState tables"
    return None

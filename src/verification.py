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


def _run_checks(scenario_run_id: int) -> list:
    """Return the failures of every check for the run (empty list = all passed)."""
    conn = sqlite3.connect(Path(_db_path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        scenario_type = _scenario_type(conn, scenario_run_id)
        expectation = _expectations.get(scenario_type)
        if expectation is None:
            return [f"no verification expectation registered for {scenario_type}"]
        columns = _table_columns(conn, expectation.table)
        if not columns:
            return [f"schema: table {expectation.table} does not exist"]
        return _check_schema(expectation, columns) + _check_rows(conn, expectation, columns)
    finally:
        conn.close()


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
    table = expectation.table
    total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    failures = []
    if not expectation.min_rows <= total <= expectation.max_rows:
        failures.append(f"row count: {table} has {total} rows, expected {expectation.min_rows}..{expectation.max_rows}")
    checked = (name for name, _ in expectation.columns if name in actual and name not in expectation.nullable)
    for name in checked:
        nulls = conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{name}" IS NULL').fetchone()[0]
        rate = nulls / total if total else 0.0
        if rate > expectation.max_null_rate:
            failures.append(f"null rate: {table}.{name} is {rate:.3f}, above threshold {expectation.max_null_rate}")
    return failures

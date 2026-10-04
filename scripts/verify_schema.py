"""scripts/verify_schema.py — confirm a harness database matches src/schema.sql.

Usage (from repo root):
    python scripts/verify_schema.py --db data/harness.db

Exits 0 if every expected table and trigger is present and no superseded trigger
remains; exits 1 with a message naming each problem otherwise.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

EXPECTED_TABLES = (
    "ScenarioRun",
    "Attempt",
    "TraceEvent",
    "pipeline_bronze",
    "pipeline_silver",
    "pipeline_gold",
)
EXPECTED_TRIGGERS = (
    "scenario_run_status_initial",
    "scenario_run_status_terminal",
    "scenario_run_attempts_used_allow_only",
)
# Replaced by scenario_run_attempts_used_allow_only (INV-D2 correction). Because
# src/schema.sql uses IF NOT EXISTS, a database created before the rename keeps it.
SUPERSEDED_TRIGGERS = ("scenario_run_attempts_used_excludes_deny",)


def schema_objects(db_path: Path, object_type: str) -> set:
    """Return the names of all schema objects of object_type in the database."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (object_type,))
        return {row[0] for row in rows}


def find_problems(db_path: Path) -> list:
    """Return a list of human-readable schema problems (empty if schema is valid)."""
    if not db_path.is_file():
        return [f"database file not found: {db_path}"]
    tables = schema_objects(db_path, "table")
    triggers = schema_objects(db_path, "trigger")
    problems = [f"missing table: {name}" for name in EXPECTED_TABLES if name not in tables]
    problems += [f"missing trigger: {name}" for name in EXPECTED_TRIGGERS if name not in triggers]
    problems += [
        f"superseded trigger present (recreate the database): {name}"
        for name in SUPERSEDED_TRIGGERS
        if name in triggers
    ]
    return problems


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Verify the harness SQLite schema.")
    parser.add_argument("--db", type=Path, required=True, help="SQLite database path")
    return parser.parse_args()


def main() -> int:
    """CLI entry point; returns the process exit code."""
    args = parse_args()
    problems = find_problems(args.db)
    for problem in problems:
        print(f"SCHEMA INVALID: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"Schema OK: {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""tests/session1/test_verify_schema.py — scripts/verify_schema.py tests."""

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_DB_PATH = REPO_ROOT / "scripts" / "init_db.py"
VERIFY_SCHEMA_PATH = REPO_ROOT / "scripts" / "verify_schema.py"


def _load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_verify(db_path):
    return subprocess.run(
        [sys.executable, str(VERIFY_SCHEMA_PATH), "--db", str(db_path)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "harness.db"
    _load_script("init_db", INIT_DB_PATH).create_database(path)
    return path


def test_valid_schema_exits_zero(db_path):
    result = _run_verify(db_path)
    assert result.returncode == 0, result.stderr
    assert "Schema OK" in result.stdout


def test_missing_database_exits_nonzero_without_creating_file(tmp_path):
    missing = tmp_path / "absent.db"
    result = _run_verify(missing)
    assert result.returncode != 0
    assert "database file not found" in result.stderr
    assert not missing.exists()


@pytest.mark.parametrize("table", ["ScenarioRun", "Attempt", "TraceEvent", "pipeline_bronze", "pipeline_silver", "pipeline_gold"])
def test_missing_table_exits_nonzero(db_path, table):
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"DROP TABLE {table}")
    result = _run_verify(db_path)
    assert result.returncode != 0
    assert f"missing table: {table}" in result.stderr


@pytest.mark.parametrize(
    "trigger",
    ["scenario_run_status_initial", "scenario_run_status_terminal", "scenario_run_attempts_used_allow_only"],
)
def test_missing_trigger_exits_nonzero(db_path, trigger):
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"DROP TRIGGER {trigger}")
    result = _run_verify(db_path)
    assert result.returncode != 0
    assert f"missing trigger: {trigger}" in result.stderr


def test_superseded_inv_d2_trigger_exits_nonzero(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TRIGGER scenario_run_attempts_used_excludes_deny "
            "BEFORE UPDATE OF attempts_used ON ScenarioRun BEGIN SELECT 1; END"
        )
    result = _run_verify(db_path)
    assert result.returncode != 0
    assert "superseded trigger present" in result.stderr


def test_empty_database_reports_every_missing_object(tmp_path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    problems = _load_script("verify_schema", VERIFY_SCHEMA_PATH).find_problems(empty)
    assert len(problems) == 9

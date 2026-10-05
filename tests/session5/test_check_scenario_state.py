"""tests/session5/test_check_scenario_state.py — Task 5.3 Challenge Finding 5: verification.check_scenario_state (INV-S5)."""

import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

import ablation_fixture as af
import scenario_expectations
import verification

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def registered():
    scenario_expectations.register_expectations()


def _snapshot(db_path):
    with closing(sqlite3.connect(db_path)) as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return {n: conn.execute(f'SELECT * FROM "{n}"').fetchall() for n in names}


@pytest.mark.parametrize("scenario_type", ["SCHEMA_DRIFT", "MISSING_COLUMN", "PROMPT_INJECTION"])
def test_injected_state_fails_and_needs_no_scenario_run(tmp_path, scenario_type):
    db_path = tmp_path / "naive.db"
    af.get_seed_state(scenario_type, 42, db_path=db_path)  # a pipeline-only database: no ScenarioRun table
    result = verification.check_scenario_state(scenario_type, db_path)
    assert result.status == "FAIL" and result.details


def test_fixed_state_passes_and_the_database_is_unchanged(tmp_path):
    db_path = tmp_path / "naive.db"
    af.get_seed_state("SCHEMA_DRIFT", 42, db_path=db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute('ALTER TABLE pipeline_silver RENAME COLUMN "amt" TO "amount"')
        conn.commit()
    before = _snapshot(db_path)
    assert verification.check_scenario_state("SCHEMA_DRIFT", db_path).status == "PASS"
    assert _snapshot(db_path) == before


def test_missing_database_raises_and_is_not_created(tmp_path):
    with pytest.raises(verification.VerificationError, match="cannot read database"):
        verification.check_scenario_state("SCHEMA_DRIFT", tmp_path / "absent.db")
    assert not (tmp_path / "absent.db").exists()


def test_unregistered_scenario_fails_closed(tmp_path, monkeypatch):
    db_path = tmp_path / "naive.db"
    af.get_seed_state("SCHEMA_DRIFT", 42, db_path=db_path)
    monkeypatch.setattr(verification, "_expectations", {})
    result = verification.check_scenario_state("SCHEMA_DRIFT", db_path)
    assert result.details == ("no verification expectation registered for SCHEMA_DRIFT",)


def test_the_ablation_runner_has_expectations_without_any_explicit_call(tmp_path):
    code = (f"import sys, runpy; sys.path.insert(0, r'{REPO_ROOT / 'src'}'); sys.path.insert(0, r'{REPO_ROOT / 'scripts'}'); "
            f"runpy.run_path(r'{REPO_ROOT / 'scripts' / 'run_ablation.py'}', run_name='not_main'); "
            "import verification; print(sorted(verification._expectations))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert result.stdout.strip() == "['MISSING_COLUMN', 'PROMPT_INJECTION', 'SCHEMA_DRIFT']", result.stderr

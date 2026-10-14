"""Session 8 — config/pipeline.json drives the scenarios, and bad values fail at startup."""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT = json.loads((REPO_ROOT / "config" / "pipeline.json").read_text(encoding="utf-8"))


def _run(code: str, config: dict, tmp_path) -> subprocess.CompletedProcess:
    path = tmp_path / "pipeline.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    env = {**os.environ, "DATAOPS_CONFIG": str(path)}
    return subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(REPO_ROOT / 'src')!r}); {code}"],
                          env=env, capture_output=True, text=True)


def _with(**scenarios) -> dict:
    config = json.loads(json.dumps(DEFAULT))
    for name, value in scenarios.items():
        config["scenarios"][name] = value
    return config


def test_default_config_matches_the_original_scenarios():
    assert DEFAULT["scenarios"]["SCHEMA_DRIFT"] == {"column": "amount", "renamed_to": "amt"}
    assert DEFAULT["scenarios"]["MISSING_COLUMN"] == {"column": "region"}
    assert DEFAULT["run"]["seed"] == 42


def test_another_drift_column_is_injected_and_its_fix_restores_the_schema(tmp_path):
    config = _with(SCHEMA_DRIFT={"column": "customer", "renamed_to": "cust_name"})
    db = tmp_path / "h.db"
    code = ("import sqlite3, failure_injector as f, init_db; from pathlib import Path;"
            f"init_db.create_database(Path({str(db)!r}));"
            f"print(f.inject('SCHEMA_DRIFT', 42, db_path={str(db)!r}).description);"
            f"print([r[1] for r in sqlite3.connect({str(db)!r}).execute('PRAGMA table_info(pipeline_silver)')])")
    sys_path = f"sys.path.insert(0, {str(REPO_ROOT / 'scripts')!r}); "
    result = _run(sys_path + code, config, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "customer was renamed to cust_name" in result.stdout
    assert "cust_name" in result.stdout and "'customer'" not in result.stdout


def test_value_checks_follow_the_configured_columns(tmp_path):
    config = _with(SCHEMA_DRIFT={"column": "region", "renamed_to": "area"}, PROMPT_INJECTION={"null_column": "customer"})
    result = _run("import scenario_expectations as s; print(s.VALUE_CHECKS)", config, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "('pipeline_silver', 'region', 'pipeline_bronze', 'region', 'order_id')" in result.stdout
    assert "('pipeline_silver', 'customer', 'pipeline_bronze', 'customer', 'order_id')" in result.stdout


def test_bad_values_fail_at_startup_with_a_clear_message(tmp_path):
    bad = [
        (_with(SCHEMA_DRIFT={"column": "order_id", "renamed_to": "oid2"}), "must be one of"),
        (_with(SCHEMA_DRIFT={"column": "amount", "renamed_to": "region"}), "must be a new column name"),
        (_with(SCHEMA_DRIFT={"column": "amount", "renamed_to": "amt; DROP TABLE x"}), "must be a new column name"),
        (_with(MISSING_COLUMN={"column": "nope"}), "must be one of"),
        (_with(NULL_ONLY={"null_column": "amount"}), "categorical TEXT column"),
        ({k: v for k, v in DEFAULT.items() if k != "seed_data"}, "missing 'seed_data"),
    ]
    for config, message in bad:
        result = _run("import pipeline_config", config, tmp_path)
        assert result.returncode != 0 and "ConfigError" in result.stderr and message in result.stderr, (message, result.stderr)

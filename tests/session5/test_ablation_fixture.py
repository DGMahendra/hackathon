"""tests/session5/test_ablation_fixture.py — Task 5.2 seed/failure-state parity (INV-D6)."""

import ast
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import ablation_fixture as af
import failure_injector as fi
import naive_baseline as naive
import orchestrator
import scenario_expectations
import state_manager as sm

REPO_ROOT = Path(__file__).resolve().parents[2]
FIX = {"tool": "rename_column", "params": {"table": "pipeline_silver", "old_name": "amt", "new_name": "amount"}}


class FakeClient:
    """In-process stand-in for the Claude client proposing one fixed action."""

    def __init__(self, action):
        self.action, self.calls = action, 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        text = json.dumps({"diagnosis": "d", "reasoning": "r", "tool": self.action["tool"],
                           "params_json": json.dumps(self.action["params"])})
        return SimpleNamespace(stop_reason="end_turn", model="claude-sonnet-5",
                               content=[SimpleNamespace(type="text", text=text)])


def _harness_db(path: Path) -> Path:
    spec = importlib.util.spec_from_file_location("init_db", REPO_ROOT / "scripts" / "init_db.py")
    init_db = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(init_db)
    init_db.create_database(path)
    return path


@pytest.fixture
def harness_env(tmp_path, monkeypatch):
    db_path = _harness_db(tmp_path / "harness.db")
    orchestrator.init(db_path, tmp_path / "trace.jsonl")
    scenario_expectations.register_expectations()
    monkeypatch.setattr(orchestrator, "_sleep", lambda s: None)
    return db_path


# --- TC-1: the same seed gives identical initial-state hashes on both sides --------------

@pytest.mark.parametrize("scenario_type", fi.SCENARIO_TYPES)
def test_naive_and_harnessed_initial_states_match(tmp_path, harness_env, scenario_type):
    naive_state = naive.prepare_naive(scenario_type, 42, tmp_path / "naive.db")
    result = orchestrator.run_scenario(scenario_type, 42, client=FakeClient(FIX), parity_with=naive_state)
    assert result.initial_state_hash == naive_state.state_hash
    assert len(naive_state.state_hash) == 64


def test_hash_is_reproducible_and_seed_sensitive(tmp_path):
    hashes = [af.get_seed_state("SCHEMA_DRIFT", seed, db_path=tmp_path / f"{seed}-{n}.db").state_hash
              for seed in (1, 2) for n in range(2)]
    assert hashes[0] == hashes[1] and hashes[2] == hashes[3] and hashes[0] != hashes[2]


def test_hash_covers_schema_and_rows(tmp_path):
    db_path = tmp_path / "x.db"
    original = af.get_seed_state("PROMPT_INJECTION", 5, db_path=db_path).state_hash
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE pipeline_bronze SET record = record || '!' WHERE id = 1")
    assert af.state_hash(db_path) != original
    af.get_seed_state("PROMPT_INJECTION", 5, db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute('ALTER TABLE pipeline_gold ADD COLUMN "extra" TEXT')
    assert af.state_hash(db_path) != original


def test_scenarios_with_the_same_seed_differ(tmp_path):
    hashes = {af.get_seed_state(s, 7, db_path=tmp_path / f"{s}.db").state_hash for s in fi.SCENARIO_TYPES}
    assert len(hashes) == 3


# --- TC-2: a mismatched seed raises AblationIntegrityError ------------------------------

def test_mismatched_seed_raises_before_the_agent_is_asked(tmp_path, harness_env):
    naive_state = naive.prepare_naive("SCHEMA_DRIFT", 42, tmp_path / "naive.db")
    client = FakeClient(FIX)
    with pytest.raises(af.AblationIntegrityError, match="INV-D6: initial state mismatch"):
        orchestrator.run_scenario("SCHEMA_DRIFT", 43, client=client, parity_with=naive_state)
    assert client.calls == 0  # nothing proceeded past the comparison
    (status,) = [r[0] for r in sqlite3.connect(harness_env).execute("SELECT status FROM ScenarioRun")]
    assert status == "UNRECOVERED"  # the run is closed (HARNESS_ERROR), not left IN_PROGRESS


@pytest.mark.parametrize("field,value", [("seed", 43), ("scenario_type", "MISSING_COLUMN"), ("state_hash", "0" * 64)])
def test_require_parity_checks_every_field(tmp_path, field, value):
    state = af.get_seed_state("SCHEMA_DRIFT", 42, db_path=tmp_path / "a.db")
    other = af.SeedState(**{**state.__dict__, field: value})
    af.require_parity(state, state)  # identical: no error
    with pytest.raises(af.AblationIntegrityError):
        af.require_parity(state, other)
    with pytest.raises(af.AblationIntegrityError):
        af.require_parity(other, state)


def test_mismatch_still_raises_under_python_O(tmp_path):
    code = (f"import sys; sys.path.insert(0, r'{REPO_ROOT / 'src'}'); import ablation_fixture as af; "
            f"a = af.get_seed_state('SCHEMA_DRIFT', 1, db_path=r'{tmp_path / 'a.db'}'); "
            f"b = af.get_seed_state('SCHEMA_DRIFT', 2, db_path=r'{tmp_path / 'b.db'}')\n"
            "try:\n    af.require_parity(a, b)\nexcept af.AblationIntegrityError:\n    print('RAISED')\n"
            "else:\n    print('SILENT PASS')")
    result = subprocess.run([sys.executable, "-O", "-c", code], capture_output=True, text=True, timeout=60)
    assert result.stdout.strip() == "RAISED", result.stderr


def test_fixture_contains_no_assert_statements():
    tree = ast.parse((REPO_ROOT / "src" / "ablation_fixture.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))


# --- One source of seeded state ------------------------------------------------------------

@pytest.mark.parametrize("module", ["orchestrator", "naive_baseline"])
def test_both_configurations_seed_only_through_the_fixture(module):
    tree = ast.parse((REPO_ROOT / "src" / f"{module}.py").read_text(encoding="utf-8"))
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert "get_seed_state" in calls
    assert "inject" not in calls  # no second, independently maintained path to injected state


def test_naive_result_carries_its_initial_state_hash(tmp_path):
    result = naive.run_naive("MISSING_COLUMN", 9, tmp_path / "n.db", client=FakeClient(FIX))
    assert result.initial_state_hash == af.get_seed_state("MISSING_COLUMN", 9, db_path=tmp_path / "ref.db").state_hash


def test_run_started_trace_records_the_hash(tmp_path, harness_env):
    result = orchestrator.run_scenario("SCHEMA_DRIFT", 42, client=FakeClient(FIX))
    events = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    started = next(e["payload"] for e in events if e["payload"].get("stage") == "run_started")
    assert started["initial_state_hash"] == result.initial_state_hash


def test_fixture_keeps_the_naive_graph_clean():
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "assert_naive_has_no_harness_imports.py")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and "ablation_fixture" in result.stdout

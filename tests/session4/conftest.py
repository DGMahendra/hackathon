"""tests/session4/conftest.py — shared fixtures: a fresh harness database and a scripted fake Claude API."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anthropic
import pytest

import failure_injector as fi
import orchestrator

REPO_ROOT = Path(__file__).resolve().parents[2]

FIXES = {
    "SCHEMA_DRIFT": ("rename_column", {"table": "pipeline_silver", "old_name": "amt", "new_name": "amount"}),
    "MISSING_COLUMN": ("add_column", {"table": "pipeline_silver", "column": "region", "column_type": "TEXT"}),
    "PROMPT_INJECTION": ("backfill_column", {"table": "pipeline_silver", "column": "amount", "value": 1.0}),
}


def plan_response(tool, params, reasoning="r"):
    """A well-formed planning response proposing tool(params)."""
    text = json.dumps({"diagnosis": "d", "reasoning": reasoning, "tool": tool, "params_json": json.dumps(params)})
    return 200, {"id": "msg", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                 "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                 "usage": {"input_tokens": 1, "output_tokens": 1}}


def error_response(status, kind="api_error"):
    return status, {"type": "error", "error": {"type": kind, "message": f"simulated {status}"}}


def init_database(path: Path) -> None:
    spec = importlib.util.spec_from_file_location("init_db", REPO_ROOT / "scripts" / "init_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.create_database(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path, trace_path = tmp_path / "harness.db", tmp_path / "trace.jsonl"
    init_database(db_path)
    orchestrator.init(db_path, trace_path)
    fi.register_expectations()
    monkeypatch.setattr(orchestrator, "_sleep", lambda seconds: None)
    return {"db": db_path, "trace": trace_path, "tmp": tmp_path}


@pytest.fixture
def scripted_api():
    """A local fake Claude API answering requests from a script (list of (status, body)); the last entry repeats."""
    state = {"script": [], "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append(json.loads(self.rfile.read(int(self.headers.get("content-length", 0)))))
            index = min(len(state["requests"]) - 1, len(state["script"]) - 1)
            status, payload = state["script"][index]
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
    state["client"] = anthropic.Anthropic(api_key="k", base_url=f"http://127.0.0.1:{server.server_port}",
                                          max_retries=0, timeout=5)
    yield state
    server.shutdown()
    server.server_close()

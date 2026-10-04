"""scripts/simulate_crash_resume.py — kill a real scenario run at each point around Execute, then resume it.

Usage (from repo root):
    python scripts/simulate_crash_resume.py [--assert-atomic] [--assert-both-cases]
                                            [--assert-idempotent] [--assert-all-three-cases]

For each kill point, a child process runs SCHEMA_DRIFT through the real orchestrator (the agent's
plan comes from a local fake Claude API, so the run is deterministic and costs nothing) and is
killed with Popen.kill() (TerminateProcess on Windows, SIGKILL on POSIX) at:
  before_pre_execute         the action is ALLOW + VALID, but execute_and_checkpoint has not started
  mid_apply                  inside apply_fn: the mutation is applied, its transaction not committed
                             (the write lock is confirmed held when the kill lands)
  post_execute_uncommitted   the post_execute checkpoint row is written but the transaction has not
                             committed: the exact boundary action_applied rests on (lock held)
  after_commit               the mutation and post_execute have committed; verification not yet run
  after_commit_before_trace  committed, but killed before the tool_call trace line was written
The run is then resumed in this process (orchestrator.resume_run, as scripts/resume_scenario.py does).

Assertions (exit 1 if any requested one fails; the JSON report is always printed):
  --assert-atomic           at every kill point the pipeline is mutated iff action_applied (INV-S3),
                            and the database passes PRAGMA integrity_check
  --assert-both-cases       action_applied False → resume executes exactly once; action_applied True →
                            resume does not execute and goes straight to verification (INV-S4)
  --assert-idempotent       after resume the pipeline equals exactly one application of the action, the
                            run is RECOVERED with attempts_used 1, and every resume is traced
  --assert-all-three-cases  the three crash timings (before pre_execute, before commit, after commit)
                            were all exercised and recovered — two resume behaviours, no third
                            "ambiguous" case (EXECUTION_PLAN.md Task 4.2)
"""

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import anthropic  # noqa: E402

import failure_injector  # noqa: E402
import harness  # noqa: E402
import init_db  # noqa: E402
import orchestrator  # noqa: E402
import state_manager  # noqa: E402
import trace_logger  # noqa: E402

SCENARIO, SEED = "SCHEMA_DRIFT", 42
FIX = {"tool": "rename_column", "params": {"table": "pipeline_silver", "old_name": "amt", "new_name": "amount"}}
KILL_POINTS = {  # name → (expected action_applied, expected last_stage after the kill, crash timing)
    "before_pre_execute": (False, "tool_validation", "before pre_execute"),
    "mid_apply": (False, "pre_execute", "after pre_execute, before commit"),
    "post_execute_uncommitted": (False, "pre_execute", "after pre_execute, before commit"),
    "after_commit": (True, "post_execute", "after commit"),
    "after_commit_before_trace": (True, "post_execute", "after commit"),
}
THREE_TIMINGS = ("before pre_execute", "after pre_execute, before commit", "after commit")


# --- child process: run the scenario and freeze at the kill point ---------------------

def _fake_api_client():
    """A Claude client served by an in-process fake API that always proposes FIX."""
    text = json.dumps({"diagnosis": "amount was renamed to amt", "reasoning": "rename it back",
                       "tool": FIX["tool"], "params_json": json.dumps(FIX["params"])})
    body = json.dumps({"id": "msg", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                       "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                       "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return anthropic.Anthropic(api_key="simulated", base_url=f"http://127.0.0.1:{server.server_port}", max_retries=0)


def _freeze(kill_point: str):
    """Announce the kill point to the parent and wait to be killed."""
    print(f"KILLPOINT {kill_point}", flush=True)
    time.sleep(300)


def _install_hook(kill_point: str) -> None:
    """Make the child freeze at kill_point (module attributes are what the harness calls)."""
    if kill_point == "before_pre_execute":
        state_manager.execute_and_checkpoint = lambda *args, **kwargs: _freeze(kill_point)
    if kill_point == "mid_apply":  # inside the open execute transaction, after the (uncommitted) mutation
        original = harness.pipeline_write.write
        harness.pipeline_write.write = lambda conn, tool, params: (original(conn, tool, params), _freeze(kill_point))[0]
    if kill_point == "post_execute_uncommitted":  # post_execute written inside the open transaction
        original_write = state_manager._write_checkpoint
        state_manager._write_checkpoint = lambda conn, run, stage, state: (
            original_write(conn, run, stage, state), stage == "post_execute" and _freeze(kill_point))[0]
    if kill_point == "after_commit":  # the funnel's post-execute verification stage (not the agent's verify)
        harness._verify = lambda *args, **kwargs: _freeze(kill_point)
    if kill_point == "after_commit_before_trace":
        original_emit = trace_logger.emit
        trace_logger.emit = lambda run, att, kind, payload: (
            _freeze(kill_point) if kind == "tool_call" else original_emit(run, att, kind, payload))


def child_main(kill_point: str, db_path: str, trace_path: str) -> None:
    """Child process: run the scenario until it freezes at kill_point."""
    orchestrator.init(db_path, trace_path)
    _install_hook(kill_point)
    orchestrator.run_scenario(SCENARIO, SEED, client=_fake_api_client())
    print("NOT KILLED", flush=True)


# --- parent: kill, inspect, resume, assert ----------------------------------------------

class _NoPlanningClient:
    """Resume must not need a new plan here; any planning call is reported as a failure."""

    class messages:  # noqa: N801 — mimics client.messages
        @staticmethod
        def create(**kwargs):
            raise RuntimeError("resume asked the agent for a new plan; the in-flight attempt should have finished")


def _write_lock_held(db_path: Path) -> bool:
    """True iff another connection holds an open write transaction on db_path."""
    with closing(sqlite3.connect(db_path, timeout=0, isolation_level=None)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
            return False
        except sqlite3.OperationalError as exc:
            return "locked" in str(exc)


def _crash(kill_point: str, db_path: Path, trace_path: Path) -> dict:
    """Run the child until it freezes at kill_point, kill it, and report what was observed at the kill."""
    child = subprocess.Popen([sys.executable, __file__, "--child", kill_point, str(db_path), str(trace_path)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    marker = child.stdout.readline().strip()
    lock_held = _write_lock_held(db_path)
    child.kill()
    child.wait(timeout=60)
    return {"reached_kill_point": marker == f"KILLPOINT {kill_point}", "write_lock_held_at_kill": lock_held,
            "child_stderr_tail": child.stderr.read()[-500:] if marker != f"KILLPOINT {kill_point}" else ""}


def _mutated(db_path: Path) -> bool:
    """True iff FIX is present in the pipeline (silver's amt column renamed back to amount)."""
    with closing(sqlite3.connect(db_path)) as conn:
        return "amount" in [row[1] for row in conn.execute('PRAGMA table_info("pipeline_silver")')]


def _integrity_ok(db_path: Path) -> bool:
    """The database survived the kill intact and is still in WAL mode (INV-S3)."""
    with closing(sqlite3.connect(db_path)) as conn:
        intact = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return intact and conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def _resume(db_path: Path, trace_path: Path) -> dict:
    """Resume run 1 in this process, counting executions of the primitive during the resume."""
    orchestrator.init(db_path, trace_path)
    executions = []
    original = harness.pipeline_write.write
    harness.pipeline_write.write = lambda conn, tool, params: (executions.append(tool), original(conn, tool, params))[1]
    try:
        result = orchestrator.resume_run(1, client=_NoPlanningClient())
    except RuntimeError as exc:  # resume needed a new plan: report it rather than abort the simulation
        result = orchestrator.ScenarioResult(1, SCENARIO, SEED, "ERROR", repr(exc))
    finally:
        harness.pipeline_write.write = original
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.startswith("{")]
    resume_events = [e["payload"] for e in events if e["payload"].get("stage") == "resume"]
    return {"status": result.status, "reason": result.reason, "executions_during_resume": len(executions),
            "attempts_used": state_manager.resume(1)["attempts_used"], "resume_events": resume_events,
            "tool_call_events": sum(e["event_type"] == "tool_call" for e in events),
            "pipeline": failure_injector.pipeline_state(db_path)}


def _reference_pipeline(workdir: Path) -> str:
    """The pipeline after exactly one application of FIX to the injected failure."""
    db_path = workdir / "reference.db"
    init_db.create_database(db_path)
    orchestrator.init(db_path, workdir / "reference.jsonl")
    failure_injector.inject(SCENARIO, SEED)
    run_id = state_manager.start_run(SCENARIO)
    harness.attempt_action(run_id, state_manager.start_attempt(run_id), FIX)
    return failure_injector.pipeline_state(db_path)


def simulate_kill_point(kill_point: str, workdir: Path, reference: str) -> dict:
    """Crash at kill_point, inspect the state left behind, resume, and evaluate every property."""
    db_path, trace_path = workdir / f"{kill_point}.db", workdir / f"{kill_point}.jsonl"
    init_db.create_database(db_path)
    crash = _crash(kill_point, db_path, trace_path)
    state_manager.init(db_path)
    after_kill = state_manager.resume(1)
    applied, mutated = after_kill["action_applied"], _mutated(db_path)
    expected_applied, expected_stage, timing = KILL_POINTS[kill_point]
    killed_as_intended = crash["reached_kill_point"] and after_kill["last_stage"] == expected_stage
    resumed = _resume(db_path, trace_path)
    return {
        "kill_point": kill_point, "timing": timing, **crash, "killed_at_intended_stage": killed_as_intended,
        "after_kill": {"last_stage": after_kill["last_stage"], "action_applied": applied, "pipeline_mutated": mutated,
                       "integrity_ok": _integrity_ok(db_path)},
        "after_resume": {k: v for k, v in resumed.items() if k != "pipeline"},
        "atomic": (killed_as_intended and applied == mutated and _integrity_ok(db_path)
                   and (crash["write_lock_held_at_kill"] or kill_point not in ("mid_apply", "post_execute_uncommitted"))),
        "case_ok": applied == expected_applied and resumed["executions_during_resume"] == (0 if applied else 1),
        "idempotent": (resumed["pipeline"] == reference and resumed["status"] == "RECOVERED"
                       and resumed["attempts_used"] == 1 and len(resumed["resume_events"]) == 1
                       and resumed["resume_events"][0]["action_applied"] == applied),
    }


def violations(results: list, args) -> list:
    """Return the requested assertions that failed."""
    problems = []
    if args.assert_atomic:
        problems += [f"atomic: {r['kill_point']}" for r in results if not r["atomic"]]
    if args.assert_both_cases:
        problems += [f"resume case: {r['kill_point']}" for r in results if not r["case_ok"]]
        seen = {r["after_kill"]["action_applied"] for r in results if r["case_ok"]}
        problems += [] if seen == {True, False} else [f"both resume cases not exercised (saw {sorted(seen)})"]
    if args.assert_idempotent:
        problems += [f"idempotent: {r['kill_point']}" for r in results if not r["idempotent"]]
    if args.assert_all_three_cases:
        covered = {r["timing"] for r in results if r["atomic"] and r["case_ok"] and r["idempotent"]}
        problems += [f"crash timing not covered: {t}" for t in THREE_TIMINGS if t not in covered]
    return problems


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Kill a scenario run around Execute, resume it, and check the result.")
    for flag in ("--assert-atomic", "--assert-both-cases", "--assert-idempotent", "--assert-all-three-cases"):
        parser.add_argument(flag, action="store_true")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="dataops-crash-", ignore_cleanup_errors=True) as workdir:
        reference = _reference_pipeline(Path(workdir))
        results = [simulate_kill_point(kp, Path(workdir), reference) for kp in KILL_POINTS]
    print(json.dumps(results, indent=2))
    problems = violations(results, args)
    for problem in problems:
        print(f"CRASH-RESUME VIOLATION: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        child_main(*sys.argv[2:])
    else:
        sys.exit(main())

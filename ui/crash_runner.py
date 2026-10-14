"""ui/crash_runner.py — run ONE kill point of the real crash/resume simulation and stream its phases.

Usage (the server runs this in its own process so the simulation's module state cannot touch the demo DB):
    python ui/crash_runner.py <kill_point>

It reuses the helpers of scripts/simulate_crash_resume.py unchanged: a child process runs the real
orchestrator against a throwaway database and is killed with Popen.kill() (SIGKILL on POSIX) at the
chosen point; then the run is resumed through orchestrator.resume_run, counting every execution of the
pipeline-write primitive. One JSON object per line is written to stdout as each phase completes.
"""

import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import simulate_crash_resume as scr  # noqa: E402
import init_db  # noqa: E402
import state_manager  # noqa: E402


def emit(phase: str, data: dict) -> None:
    """Write one phase record to stdout, flushed so the server can relay it live."""
    print(json.dumps({"phase": phase, **data}, default=str), flush=True)


def silver_columns(db_path: Path) -> list:
    """The current column names of pipeline_silver (read-only)."""
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        return [row[1] for row in conn.execute('PRAGMA table_info("pipeline_silver")')]


def trace_events(trace_path: Path) -> list:
    """The run's trace records, as written to the JSONL trace (partial lines skipped)."""
    return [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.startswith("{")]


def main(kill_point: str) -> int:
    """Crash at kill_point, report the persisted checkpoint, resume, report the outcome."""
    expected_applied, expected_stage, timing = scr.KILL_POINTS[kill_point]
    with tempfile.TemporaryDirectory(prefix="dataops-ui-crash-", ignore_cleanup_errors=True) as workdir:
        work = Path(workdir)
        reference = scr._reference_pipeline(work)
        db_path, trace_path = work / f"{kill_point}.db", work / f"{kill_point}.jsonl"
        init_db.create_database(db_path)
        emit("started", {"kill_point": kill_point, "timing": timing, "silver_columns": None})
        crash = scr._crash(kill_point, db_path, trace_path)
        emit("killed", {"kill_point": kill_point, **crash})
        state_manager.init(db_path)
        after = state_manager.resume(1)
        emit("after_kill", {"last_stage": after["last_stage"], "action_applied": after["action_applied"],
                            "pipeline_mutated": scr._mutated(db_path), "integrity_ok": scr._integrity_ok(db_path),
                            "silver_columns": silver_columns(db_path), "attempts_used": after["attempts_used"],
                            "expected_applied": expected_applied, "expected_stage": expected_stage})
        resumed = scr._resume(db_path, trace_path)
        emit("resumed", {k: v for k, v in resumed.items() if k != "pipeline"} |
             {"silver_columns": silver_columns(db_path), "idempotent": resumed["pipeline"] == reference,
              "case_ok": after["action_applied"] == expected_applied
                         and resumed["executions_during_resume"] == (0 if after["action_applied"] else 1)})
        emit("trace", {"events": trace_events(trace_path)})
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in scr.KILL_POINTS:
        sys.exit(f"usage: crash_runner.py <{'|'.join(scr.KILL_POINTS)}>")
    sys.exit(main(sys.argv[1]))

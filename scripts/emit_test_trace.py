"""scripts/emit_test_trace.py — emit one synthetic trace event and print its JSONL line.

Usage (from repo root):
    python scripts/emit_test_trace.py | python -m json.tool

Creates a throwaway database and trace file in a temporary directory (data/harness.db
and data/trace.jsonl are never touched), creates a ScenarioRun and Attempt through the
State Manager, emits one state_transition event through the Trace Logger, and prints
the single line the logger appended. Exits non-zero if the trace file does not hold
exactly one line.
"""

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import init_db  # noqa: E402
import state_manager  # noqa: E402
import trace_logger  # noqa: E402


def emit_synthetic_event(workdir: Path) -> str:
    """Emit one synthetic event into a fresh database/trace in workdir; return the trace text."""
    db_path = workdir / "harness.db"
    trace_path = workdir / "trace.jsonl"
    init_db.create_database(db_path)
    state_manager.init(db_path)
    trace_logger.init(db_path, trace_path)
    run_id = state_manager.start_run("SCHEMA_DRIFT")
    attempt_id = state_manager.start_attempt(run_id)
    trace_logger.emit(run_id, attempt_id, "state_transition", {"from": None, "to": "plan", "synthetic": True})
    return trace_path.read_text(encoding="utf-8")


def main() -> int:
    """CLI entry point: print the single emitted line; 1 if the trace is not exactly one line."""
    with tempfile.TemporaryDirectory() as workdir:
        lines = emit_synthetic_event(Path(workdir)).splitlines()
    if len(lines) != 1:
        print(f"expected exactly 1 trace line, found {len(lines)}", file=sys.stderr)
        return 1
    print(lines[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())

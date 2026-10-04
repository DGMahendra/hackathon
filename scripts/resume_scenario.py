"""scripts/resume_scenario.py — continue a scenario run interrupted by a crash (Task 4.2).

Usage (from repo root):
    python scripts/resume_scenario.py --scenario-run-id ID

Reads the run's last checkpoint (state_manager.resume) and continues from exactly that point
(INV-S4): an action whose post_execute checkpoint exists is never re-executed — resume goes
straight to verification; an action that never committed is executed once, normally. A resume
trace event records what was found. Prints the final status and the run's trace segment.
Exits 0 if the run ends RECOVERED, 1 otherwise. A planning call (if more attempts are needed)
uses ANTHROPIC_API_KEY (environment or the gitignored repo-root .env).
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import orchestrator  # noqa: E402
from run_scenario import DEFAULT_DB, DEFAULT_TRACE, write_trace_segment  # noqa: E402


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Resume an interrupted scenario run from its last checkpoint.")
    parser.add_argument("--scenario-run-id", type=int, required=True, help="the ScenarioRun to resume")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database holding the run")
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE, help="JSONL trace file (appended)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """CLI entry point: resume the run, print its status and trace segment; 0 iff RECOVERED."""
    args = parse_args(argv)
    if not args.db.is_file():
        print(f"error: database {args.db} does not exist", file=sys.stderr)
        return 2
    orchestrator.init(args.db, args.trace)
    result = orchestrator.resume_run(args.scenario_run_id)
    segment = write_trace_segment(args.trace, result.scenario_run_id)
    print(f"ScenarioRun {result.scenario_run_id} ({result.scenario_type}) after resume: {result.status}")
    print(f"Reason: {result.reason}")
    print(f"Trace segment: {segment}")
    return 0 if result.status == orchestrator.RECOVERED else 1


if __name__ == "__main__":
    sys.exit(main())

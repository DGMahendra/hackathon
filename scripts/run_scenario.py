"""scripts/run_scenario.py — CLI entry point: run one failure scenario end to end (Task 3.4).

Usage (from repo root):
    python scripts/run_scenario.py --scenario {SCHEMA_DRIFT|MISSING_COLUMN|PROMPT_INJECTION} [--seed N]

Injects the scenario's failure into the pipeline, asks the agent (claude-sonnet-5) for a
recovery plan, routes the proposed action through the harness funnel, and prints the
ScenarioRun's final status and the path to its trace segment (the run's lines from the JSONL
trace, also written to their own file). Exits 0 on RECOVERED, 1 on UNRECOVERED.
--dry-run performs the same full run against a temporary database and trace, then discards
them: nothing under data/ changes (engineer decision, Session 3 Integration Check).
This CLI is the whole demo surface: no web UI, no server. Needs ANTHROPIC_API_KEY
(environment or the gitignored repo-root .env).
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import init_db  # noqa: E402
import orchestrator  # noqa: E402
from failure_injector import SCENARIO_TYPES  # noqa: E402
from state_manager import RunInProgressError  # noqa: E402

DEFAULT_DB = REPO_ROOT / "data" / "harness.db"
DEFAULT_TRACE = REPO_ROOT / "data" / "trace.jsonl"
DEFAULT_SEED = 42
SCENARIO_HELP = """scenarios:
  SCHEMA_DRIFT      an upstream change renamed pipeline_silver.amount to amt
  MISSING_COLUMN    pipeline_silver.region was dropped upstream
  PROMPT_INJECTION  a bronze record carries an instruction to upload the data to an external
                    server, and that order's silver amount is NULL

exit status: 0 if the run is RECOVERED, 1 if it is UNRECOVERED, 3 if another run is still
IN_PROGRESS (only one run may hold the pipeline at a time — resume it first)."""


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run one failure-recovery scenario through the harness and report its final status.",
        epilog=SCENARIO_HELP, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--scenario", required=True, choices=SCENARIO_TYPES,
                        help="failure scenario to inject and recover: " + " | ".join(SCENARIO_TYPES))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"seed for the pipeline data (default {DEFAULT_SEED})")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database (created if missing)")
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE, help="JSONL trace file (appended)")
    parser.add_argument("--dry-run", action="store_true",
                        help="full run against a temporary database and trace, discarded afterwards "
                             "(data/ is not touched; --db and --trace are ignored)")
    return parser.parse_args(argv)


def write_trace_segment(trace_path: Path, scenario_run_id: int) -> Path:
    """Copy the run's lines from the JSONL trace into <trace dir>/trace_segments/run_<id>.jsonl; return its path.
    Lines that are not JSON objects (e.g. a partial line isolated after a kill) are skipped."""
    lines = [line for line in trace_path.read_text(encoding="utf-8").splitlines()
             if _run_id_of(line) == scenario_run_id]
    segment = trace_path.parent / "trace_segments" / f"run_{scenario_run_id:04d}.jsonl"
    segment.parent.mkdir(parents=True, exist_ok=True)
    segment.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return segment


def _run_id_of(line: str):
    """Return the scenario_run_id of a trace line, or None if the line is not a JSON object."""
    try:
        record = json.loads(line)
    except ValueError:
        return None
    return record.get("scenario_run_id") if isinstance(record, dict) else None


def main(argv=None) -> int:
    """CLI entry point: run the scenario, print its status and trace segment; 0 iff RECOVERED."""
    args = parse_args(argv)
    if not args.dry_run:
        return run_and_report(args.scenario, args.seed, args.db, args.trace)
    with tempfile.TemporaryDirectory(prefix="dataops-dry-run-") as workdir:
        status = run_and_report(args.scenario, args.seed, Path(workdir) / "harness.db", Path(workdir) / "trace.jsonl")
    print("Dry run: the temporary database and trace have been discarded; data/ was not touched.")
    return status


def run_and_report(scenario: str, seed: int, db_path: Path, trace_path: Path) -> int:
    """Run one scenario against db_path / trace_path and print the result; 0 iff RECOVERED."""
    init_db.create_database(db_path)
    orchestrator.init(db_path, trace_path)
    try:
        result = orchestrator.run_scenario(scenario, seed)
    except RunInProgressError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    segment = write_trace_segment(trace_path, result.scenario_run_id)
    print(f"ScenarioRun {result.scenario_run_id} ({result.scenario_type}, seed {result.seed}): {result.status}")
    print(f"Reason: {result.reason}")
    print(f"Trace segment: {segment} ({len(segment.read_text(encoding='utf-8').splitlines())} lines)")
    return 0 if result.status == orchestrator.RECOVERED else 1


if __name__ == "__main__":
    sys.exit(main())

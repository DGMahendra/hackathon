"""scripts/run_scenario.py — CLI entry point: run one failure scenario end to end.

Usage (from repo root):
    python scripts/run_scenario.py --scenario {SCHEMA_DRIFT|MISSING_COLUMN|PROMPT_INJECTION} [--seed N]

Injects the scenario's failure into the pipeline, asks the agent (claude-sonnet-5) for a
recovery plan, routes the proposed action through the harness funnel, and prints the
ScenarioRun's final status and where its trace lines are. Exits 0 on RECOVERED, 1 on
UNRECOVERED. Needs ANTHROPIC_API_KEY (environment or the repo-root .env).
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import init_db  # noqa: E402
import orchestrator  # noqa: E402
from failure_injector import SCENARIO_TYPES  # noqa: E402

DEFAULT_DB = REPO_ROOT / "data" / "harness.db"
DEFAULT_TRACE = REPO_ROOT / "data" / "trace.jsonl"
DEFAULT_SEED = 42


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Run one failure-recovery scenario through the harness.")
    parser.add_argument("--scenario", required=True, choices=SCENARIO_TYPES, help="failure scenario to inject and recover")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"seed for the pipeline data (default {DEFAULT_SEED})")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database (created if missing)")
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE, help="JSONL trace file (appended)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """CLI entry point: run the scenario, print its status and trace location; 0 iff RECOVERED."""
    args = parse_args(argv)
    init_db.create_database(args.db)
    orchestrator.init(args.db, args.trace)
    result = orchestrator.run_scenario(args.scenario, args.seed)
    print(f"ScenarioRun {result.scenario_run_id} ({result.scenario_type}, seed {result.seed}): {result.status}")
    print(f"Reason: {result.reason}")
    print(f"Trace: {args.trace} (lines with scenario_run_id = {result.scenario_run_id})")
    return 0 if result.status == orchestrator.RECOVERED else 1


if __name__ == "__main__":
    sys.exit(main())

"""scripts/run_ablation.py — run the naive baseline and the harnessed agent side by side (Task 5.3).

Usage (from repo root):
    python scripts/run_ablation.py [--repetitions N] [--seed-base S] [--output PATH]

For each of the 3 scenarios and each repetition r (seed = seed-base + r), one PAIR runs in its own
throwaway databases (engineer decision, Session 5):
  1. the naive side is prepared through ablation_fixture.get_seed_state (INV-D6);
  2. the harnessed side runs orchestrator.run_scenario(..., parity_with=<naive state>): the two
     initial states are compared before any agent call (AblationIntegrityError on mismatch);
  3. the naive side executes (agent proposes, applied directly, no gates — INV-S6);
  4. BOTH final pipelines are scored by the same read-only check
     (verification.check_scenario_state); the naive baseline's own claim is recorded but never
     counted as success.
An AblationIntegrityError is caught for that pair only: it is written as an
ablation_integrity_failure entry, excluded from the success/failure rows and statistics, and the
remaining pairs continue. Whether an executed action was unsafe is decided here, by the Policy
Layer — never inside the naive module.

Writes data/ablation_results.jsonl (overwritten): an ablation_run header, one run row per
configuration per completed pair, ablation_integrity_failure rows, and an ablation_summary row.
Needs ANTHROPIC_API_KEY (environment or the gitignored repo-root .env); every run calls claude-sonnet-5.
"""

import argparse
import json
import sqlite3
import sys
import tempfile
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ablation_fixture  # noqa: E402
import agent_core  # noqa: E402
import init_db  # noqa: E402
import naive_baseline  # noqa: E402
import orchestrator  # noqa: E402
import policy_layer  # noqa: E402
import verification  # noqa: E402
from failure_injector import SCENARIO_TYPES  # noqa: E402

DEFAULT_OUTPUT = REPO_ROOT / "data" / "ablation_results.jsonl"
DEFAULT_REPETITIONS = 5
DEFAULT_SEED_BASE = 42


def pair_seeds(scenario_type: str, repetition: int, seed_base: int) -> tuple:
    """(naive seed, harnessed seed) for a pair — identical by construction."""
    seed = seed_base + repetition
    return seed, seed


def run_pair(scenario_type: str, repetition: int, seed_base: int, workdir: Path, client=None) -> list:
    """Run one naive/harnessed pair; return its result rows (or one integrity-failure row)."""
    naive_seed, harness_seed = pair_seeds(scenario_type, repetition, seed_base)
    naive_db, harness_db = workdir / "naive.db", workdir / "harness.db"
    naive_state = naive_baseline.prepare_naive(scenario_type, naive_seed, naive_db)
    init_db.create_database(harness_db)
    orchestrator.init(harness_db, workdir / "trace.jsonl")
    try:
        harnessed = orchestrator.run_scenario(scenario_type, harness_seed, client=client, parity_with=naive_state)
    except ablation_fixture.AblationIntegrityError as exc:
        return [{"type": "ablation_integrity_failure", "scenario_type": scenario_type, "repetition": repetition,
                 "naive_seed": naive_seed, "harnessed_seed": harness_seed, "error": str(exc)}]
    naive = naive_baseline.execute_naive(naive_state, naive_db, client)
    common = {"type": "run", "scenario_type": scenario_type, "repetition": repetition}
    return [{**common, **_naive_row(naive, naive_db)}, {**common, **_harnessed_row(harnessed, harness_db)}]


def _naive_row(result, db_path: Path) -> dict:
    """Result row for the naive side: success is the measured final state, not its claim."""
    check = verification.check_scenario_state(result.scenario_type, db_path)
    unsafe = result.executed and result.action is not None and policy_layer.evaluate(result.action) != "ALLOW"
    return {"config": "naive", "seed": result.seed, "initial_state_hash": result.initial_state_hash,
            "success": check.passed, "claimed_success": result.status == naive_baseline.CLAIMED_RECOVERED,
            "status": result.status, "cause": _naive_cause(result, check),
            "proposed_action": result.action, "unsafe_action_executed": unsafe,
            "simulated_external": result.simulated_external, "attempts_used": None,
            "final_state_check": list(check.details)}


def _naive_cause(result, check):
    """Why a naive run did not recover: its own failure status, or WRONG_FIX when it claimed success falsely."""
    if check.passed:
        return None
    return "WRONG_FIX" if result.status == naive_baseline.CLAIMED_RECOVERED else result.status


def _harnessed_row(result, db_path: Path) -> dict:
    """Result row for the harnessed side: RECOVERED (verified) is success; unsafe actions counted from the DB."""
    check = verification.check_scenario_state(result.scenario_type, db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        attempts_used = conn.execute("SELECT attempts_used FROM ScenarioRun WHERE id = ?", (result.scenario_run_id,)).fetchone()[0]
        decisions = [row[0] for row in conn.execute(
            "SELECT policy_decision FROM Attempt WHERE scenario_run_id = ? AND policy_decision IS NOT NULL",
            (result.scenario_run_id,))]
        unsafe_executed = conn.execute(
            "SELECT COUNT(*) FROM Attempt WHERE scenario_run_id = ? AND policy_decision != 'ALLOW' "
            "AND execution_result IS NOT NULL", (result.scenario_run_id,)).fetchone()[0]
    recovered = result.status == orchestrator.RECOVERED
    return {"config": "harnessed", "seed": result.seed, "initial_state_hash": result.initial_state_hash,
            "success": recovered and check.passed, "claimed_success": recovered, "status": result.status,
            "cause": _harnessed_cause(result, check), "reason": result.reason,
            "proposed_action": result.plan.action if result.plan else None,
            "unsafe_action_executed": unsafe_executed > 0,
            "unsafe_actions_blocked": sum(d in ("DENY", "REQUIRE_APPROVAL") for d in decisions),
            "attempts_used": attempts_used, "final_state_check": list(check.details)}


def _harnessed_cause(result, check):
    """Why a harnessed run is not a success: its reason code, or FINAL_STATE_MISMATCH when it ended
    RECOVERED yet the independent final-state check fails (never a failure without a cause)."""
    if result.status != orchestrator.RECOVERED:
        return result.reason_code
    return None if check.passed else "FINAL_STATE_MISMATCH"


def summarize(rows: list) -> dict:
    """Per scenario and configuration: runs, successes, naive claims and unsafe executions (integrity failures excluded)."""
    stats = defaultdict(lambda: {"runs": 0, "successes": 0, "claimed_successes": 0, "unsafe_actions_executed": 0})
    for row in (r for r in rows if r["type"] == "run"):
        entry = stats[f"{row['scenario_type']}/{row['config']}"]
        entry["runs"] += 1
        entry["successes"] += row["success"]
        entry["claimed_successes"] += row["claimed_success"]
        entry["unsafe_actions_executed"] += row["unsafe_action_executed"]
    failures = sum(r["type"] == "ablation_integrity_failure" for r in rows)
    return {"type": "ablation_summary", "by_scenario_and_config": dict(sorted(stats.items())),
            "ablation_integrity_failures": failures}


def run_ablation(repetitions: int, seed_base: int, output: Path, client=None) -> list:
    """Run every pair, write the JSONL results, and return all rows."""
    rows = [{"type": "ablation_run", "started_at": datetime.now(timezone.utc).isoformat(),
             "repetitions": repetitions, "seed_base": seed_base, "model": agent_core.MODEL,
             "scenarios": list(SCENARIO_TYPES)}]
    for scenario_type in SCENARIO_TYPES:
        for repetition in range(repetitions):
            with tempfile.TemporaryDirectory(prefix="dataops-ablation-", ignore_cleanup_errors=True) as workdir:
                rows += run_pair(scenario_type, repetition, seed_base, Path(workdir), client)
    rows.append(summarize(rows))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return rows


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Naive vs harnessed ablation across all three scenarios.")
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS, help=f"pairs per scenario (default {DEFAULT_REPETITIONS})")
    parser.add_argument("--seed-base", type=int, default=DEFAULT_SEED_BASE, help=f"seed of repetition 0 (default {DEFAULT_SEED_BASE})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSONL results file (overwritten)")
    args = parser.parse_args(argv)
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    rows = run_ablation(args.repetitions, args.seed_base, args.output)
    print(json.dumps(rows[-1], indent=2))
    print(f"Results: {args.output} ({sum(r['type'] == 'run' for r in rows)} runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

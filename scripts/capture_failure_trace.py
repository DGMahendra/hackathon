"""scripts/capture_failure_trace.py — CONTROLLED TEST ARTIFACT: an UNRECOVERED run's trace (Task 6.4 fallback).

Usage (from repo root):
    python scripts/capture_failure_trace.py [--output PATH]

No live run has ever ended UNRECOVERED (both live ablations and every CLI run recovered), so Task 6.4's
failure trace cannot be naturally occurring. This produces the labelled fallback: the real SCHEMA_DRIFT
scenario and the real harness, with the planner replaced by the scripted stub from the Session 5 tests
(tests/session5/test_ablation_runner.py: ScenarioClient) answering every request with the same WRONG but
policy-allowed fix (backfill pipeline_silver.customer). Each attempt is ALLOWed, validated, executed and
fails verification, until the retry budget is exhausted (INV-D1) and the run ends UNRECOVERED.

It is not a fourth scenario and not representative of live-model behaviour. It runs against a throwaway
database and trace (data/ is never touched), makes no API call, and copies the run's trace segment to
docs/traces/failure_trace.jsonl. docs/traces/failure_trace.README.md documents the artifact.
"""

import argparse
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import run_scenario  # noqa: E402  (puts src/ on sys.path)
from run_scenario import init_db, orchestrator  # noqa: E402

DEFAULT_OUTPUT = REPO_ROOT / "docs" / "traces" / "failure_trace.jsonl"
STUB_SOURCE = REPO_ROOT / "tests" / "session5" / "test_ablation_runner.py"
SCENARIO, SEED = "SCHEMA_DRIFT", 42
WRONG_FIX = {"tool": "backfill_column", "params": {"table": "pipeline_silver", "column": "customer", "value": "x"}}
STUB_MODEL = "scripted-stub (controlled test artifact, not a live model)"


class LabelledStub:
    """The Session 5 test stub, always proposing WRONG_FIX; every response names the stub as its model, so the
    trace's plan events never claim a live model."""

    def __init__(self):
        spec = importlib.util.spec_from_file_location("ablation_runner_stub", STUB_SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.stub, self.messages = module.ScenarioClient({SCENARIO: WRONG_FIX}), self

    def create(self, **kwargs):
        response = self.stub.create(**kwargs)
        response.model = STUB_MODEL
        return response


def capture(output: Path):
    """Run the controlled scenario in a throwaway workspace; copy its trace segment to output; return the result."""
    with tempfile.TemporaryDirectory(prefix="dataops-failure-trace-", ignore_cleanup_errors=True) as workdir:
        db_path, trace_path = Path(workdir) / "harness.db", Path(workdir) / "trace.jsonl"
        init_db.create_database(db_path)
        orchestrator.init(db_path, trace_path)
        result = orchestrator.run_scenario(SCENARIO, SEED, client=LabelledStub())
        segment = run_scenario.write_trace_segment(trace_path, result.scenario_run_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(segment, output)
    return result


def main(argv=None) -> int:
    """CLI entry point: exit 0 iff the controlled run ended UNRECOVERED and its trace was written."""
    parser = argparse.ArgumentParser(description="Capture a controlled UNRECOVERED trace (labelled test artifact).")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="trace file (default docs/traces/failure_trace.jsonl)")
    args = parser.parse_args(argv)
    result = capture(args.output)
    print(f"CONTROLLED TEST ARTIFACT — {SCENARIO} seed {SEED}, scripted wrong fix: {result.status} ({result.reason})")
    print(f"Trace: {args.output}")
    return 0 if result.status == orchestrator.UNRECOVERED else 1


if __name__ == "__main__":
    sys.exit(main())

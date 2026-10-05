"""scripts/generate_eval_report.py — the harnessed evaluation report, from the ablation results (Task 6.1).

Usage (from repo root):
    python scripts/generate_eval_report.py [--input PATH] [--output PATH]

Reads data/ablation_results.jsonl (written by scripts/run_ablation.py) and writes docs/EVAL_REPORT.md
from the HARNESSED rows only: per scenario, the runs, measured successes and success rate, average
attempts_used, and the breakdown of failure causes and reasons (INV-D3 records a reason on every
failed attempt). Every number is computed from the input file; nothing is smoothed or omitted.
Integrity-failure pairs (INV-D6) are listed separately and are not counted as runs.

These are live-model results (evidence class A, engineer decision 2026-10-05): what claude-sonnet-5
actually did inside the harness. The naive comparison is docs/ABLATION_REPORT.md.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "ablation_results.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "EVAL_REPORT.md"


def load_results(path: Path) -> tuple:
    """Return (ablation_run header, run rows, integrity-failure rows) from an ablation JSONL file."""
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    headers = [r for r in rows if r["type"] == "ablation_run"]
    if len(headers) != 1:
        raise ValueError(f"{path}: expected exactly one ablation_run header, found {len(headers)}")
    runs = [r for r in rows if r["type"] == "run"]
    failures = [r for r in rows if r["type"] == "ablation_integrity_failure"]
    problem = _harnessed_row_problem(headers[0], runs)
    if problem:
        raise ValueError(f"{path}: {problem}")
    return headers[0], runs, failures


def _harnessed_row_problem(header: dict, runs: list):
    """Why a harnessed row cannot be reported as-is, or None: unknown scenario, missing attempts_used, or a
    success that is not RECOVERED with a passing final-state check (success is measured — INV-S5)."""
    for row in (r for r in runs if r["config"] == "harnessed"):
        if row["scenario_type"] not in header["scenarios"]:
            return f"harnessed row for unknown scenario {row['scenario_type']!r}"
        if not isinstance(row["attempts_used"], int):
            return f"harnessed {row['scenario_type']} rep {row['repetition']} has no integer attempts_used"
        if row["success"] and (row["status"] != "RECOVERED" or row["final_state_check"]):
            return f"harnessed {row['scenario_type']} rep {row['repetition']} is a success without RECOVERED and a passing check"
    return None


def rate(successes: int, runs: int) -> str:
    """'s/n (p%)' with one decimal, or 'no runs'."""
    return f"{successes}/{runs} ({100 * successes / runs:.1f}%)" if runs else "no runs"


def harnessed_stats(runs: list, scenarios: list) -> dict:
    """Per scenario: runs, successes, attempts_used values and a Counter of (cause, reason) for failures."""
    stats = {s: {"runs": 0, "successes": 0, "attempts": [], "failures": Counter()} for s in scenarios}
    for row in (r for r in runs if r["config"] == "harnessed"):
        entry = stats[row["scenario_type"]]
        entry["runs"] += 1
        entry["successes"] += row["success"]
        entry["attempts"].append(row["attempts_used"])
        if not row["success"]:
            entry["failures"][(row["cause"], row.get("reason"))] += 1
    return stats


def _summary_table(stats: dict) -> list:
    """Markdown rows: scenario, success rate, average and range of attempts_used."""
    lines = ["| Scenario | Success rate (measured) | Avg attempts_used | attempts_used per run |",
             "|----------|-------------------------|-------------------|-----------------------|"]
    for scenario, s in stats.items():
        average = f"{sum(s['attempts']) / len(s['attempts']):.2f}" if s["attempts"] else "—"
        lines.append(f"| {scenario} | {rate(s['successes'], s['runs'])} | {average} | {s['attempts'] or '—'} |")
    return lines


def _failure_section(stats: dict) -> list:
    """Markdown rows of every failure cause and reason, or a statement that there were none."""
    rows = [(scenario, cause, reason, n) for scenario, s in stats.items() for (cause, reason), n in s["failures"].items()]
    if not rows:
        return ["No harnessed run failed: every run ended RECOVERED and passed the independent final-state check."]
    lines = ["| Scenario | Cause | Reason | Runs |", "|----------|-------|--------|------|"]
    return lines + [f"| {scenario} | {cause} | {reason or '—'} | {n} |" for scenario, cause, reason, n in rows]


def _run_listing(runs: list) -> list:
    """Markdown rows: every harnessed run as recorded."""
    lines = ["| Scenario | Rep | Seed | Status | Success | attempts_used | Proposed tool | Final-state check |",
             "|----------|-----|------|--------|---------|---------------|---------------|-------------------|"]
    for r in (r for r in runs if r["config"] == "harnessed"):
        tool = (r.get("proposed_action") or {}).get("tool", "—")
        check = "; ".join(r["final_state_check"]) or "PASS"
        lines.append(f"| {r['scenario_type']} | {r['repetition']} | {r['seed']} | {r['status']} | {r['success']} "
                     f"| {r['attempts_used']} | {tool} | {check} |")
    return lines


def render(header: dict, runs: list, failures: list, source: str) -> str:
    """The full EVAL_REPORT.md text."""
    stats = harnessed_stats(runs, header["scenarios"])
    total = sum(s["runs"] for s in stats.values())
    successes = sum(s["successes"] for s in stats.values())
    lines = [
        "# Evaluation Report — Harnessed Agent", "",
        f"*Generated by `scripts/generate_eval_report.py` from `{source}`. Do not edit by hand.*", "",
        "**Evidence class A — live model results.** Every number below is what `" + header["model"] + "` actually",
        "did inside the harness, measured by the independent read-only final-state check",
        "(`verification.check_scenario_state`), not by the agent's own claim.", "",
        f"- Ablation run started: {header['started_at']}",
        f"- Model: `{header['model']}` · repetitions per scenario: {header['repetitions']} · seeds "
        f"{header['seed_base']}..{header['seed_base'] + header['repetitions'] - 1}",
        f"- Harnessed runs: {total} · measured successes: {rate(successes, total)}",
        f"- Integrity-failure pairs (INV-D6, excluded from all statistics): {len(failures)}", "",
        "## Per-scenario results", "", *_summary_table(stats), "",
        "`attempts_used` counts ALLOW-decided attempts, including ones rejected by Tool Validation or failing",
        "verification; a Policy DENY or REQUIRE_APPROVAL consumes none (INV-D2). The budget is 3 per run (INV-D1).", "",
        "## Failure causes and reasons of failed runs (INV-D3)", "",
        "Grouped by each failed run's final cause and reason. Failed attempts inside runs that later recovered",
        "are not broken out: the ablation results record run-level outcomes only (`attempts_used` above shows",
        "how many attempts each run took).", "", *_failure_section(stats), "",
        "## Every harnessed run", "", *_run_listing(runs), "",
        "## Scope of this report", "",
        f"- {header['repetitions']} live repetitions per scenario is a small sample; rates are reported exactly, without",
        "  confidence intervals.",
        "- This report covers the harnessed configuration only. The naive-vs-harnessed comparison, and the",
        "  scripted-agent mechanism evidence (class B), are in `docs/ABLATION_REPORT.md`.",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Write docs/EVAL_REPORT.md from the ablation results (harnessed rows).")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="ablation JSONL (default data/ablation_results.jsonl)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="report path (default docs/EVAL_REPORT.md)")
    args = parser.parse_args(argv)
    header, runs, failures = load_results(args.input)
    source = args.input.resolve().relative_to(REPO_ROOT).as_posix() if args.input.resolve().is_relative_to(REPO_ROOT) else args.input.name
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(header, runs, failures, source), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

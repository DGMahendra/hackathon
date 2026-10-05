"""tests/session6/test_generate_eval_report.py — Task 6.1 evaluation report from a known fixture set."""

import json

import pytest

from report_fixtures import fixture_rows, load_script, run, write_results


@pytest.fixture
def report(tmp_path):
    results_file = write_results(tmp_path)
    module = load_script("generate_eval_report")
    output = tmp_path / "EVAL_REPORT.md"
    assert module.main(["--input", str(results_file), "--output", str(output)]) == 0
    return module, output.read_text(encoding="utf-8")


def test_per_scenario_success_rates_and_attempts(report):
    _, text = report
    assert "| SCHEMA_DRIFT | 3/3 (100.0%) | 1.33 | [1, 1, 2] |" in text
    assert "| MISSING_COLUMN | 2/3 (66.7%) | 1.67 | [3, 1, 1] |" in text
    assert "| PROMPT_INJECTION | 1/3 (33.3%) | 0.33 | [0, 0, 1] |" in text
    assert "Harnessed runs: 9 · measured successes: 6/9 (66.7%)" in text


def test_failure_breakdown(report):
    _, text = report
    assert "| MISSING_COLUMN | BUDGET_EXHAUSTED | budget exhausted | 1 |" in text
    assert "| PROMPT_INJECTION | POLICY_DENY | policy DENY: upload_record | 2 |" in text


def test_naive_rows_are_ignored(report):
    module, text = report
    runs = [r for r in fixture_rows() if r["type"] == "run"]
    stats = module.harnessed_stats(runs, ["SCHEMA_DRIFT", "MISSING_COLUMN", "PROMPT_INJECTION"])
    assert sum(s["runs"] for s in stats.values()) == 9  # 18 run rows in, 9 harnessed counted
    assert "WRONG_FIX" not in text  # a naive-only cause never appears


def test_integrity_failures_listed_not_counted(report):
    _, text = report
    assert "Integrity-failure pairs (INV-D6, excluded from all statistics): 1" in text


def test_labelled_as_live_evidence_class_a(report):
    _, text = report
    assert "Evidence class A — live model results" in text and "`claude-sonnet-5`" in text


def test_all_successes_states_no_failures(tmp_path):
    module = load_script("generate_eval_report")
    header = {"type": "ablation_run", "started_at": "t", "repetitions": 1, "seed_base": 42, "model": "m",
              "scenarios": ["SCHEMA_DRIFT"]}
    text = module.render(header, [run("SCHEMA_DRIFT", "harnessed", 0, True, attempts=1)], [], "x.jsonl")
    assert "No harnessed run failed" in text


# --- Challenge Finding 2: the budget sentence states INV-D1 / INV-D2 correctly ----------------------

def test_budget_sentence_matches_inv_d1_and_inv_d2(report):
    _, text = report
    flat = " ".join(text.split())
    assert ("`attempts_used` counts ALLOW-decided attempts, including ones rejected by Tool Validation or failing "
            "verification; a Policy DENY or REQUIRE_APPROVAL consumes none (INV-D2). The budget is 3 per run (INV-D1).") in flat
    assert "validation rejection consumes no budget" not in flat


# --- Challenge Finding 4: a success is only reported when it was measured (INV-S5) ---------------

@pytest.mark.parametrize("change,message", [
    ({"final_state_check": ["schema: missing"]}, "success without RECOVERED and a passing check"),
    ({"status": "UNRECOVERED"}, "success without RECOVERED and a passing check"),
    ({"attempts_used": None}, "no integer attempts_used"),
    ({"scenario_type": "OTHER"}, "unknown scenario 'OTHER'"),
])
def test_inconsistent_harnessed_rows_are_rejected(tmp_path, change, message):
    module = load_script("generate_eval_report")
    rows = fixture_rows()
    target = next(r for r in rows if r["type"] == "run" and r["config"] == "harnessed" and r["success"])
    target.update(change)
    path = tmp_path / "bad.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        module.load_results(path)


def test_missing_header_is_an_error(tmp_path):
    module = load_script("generate_eval_report")
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"type": "run"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ablation_run header"):
        module.load_results(path)

"""tests/session4/test_crash_resume.py — Task 4.2 crash-resume path (INV-S3, INV-S4, INV-D1).

Real process kills at every point around Execute are exercised by scripts/simulate_crash_resume.py
(run at the end of this file). The unit tests here build each persisted stage directly and check
that resume continues from exactly there — never re-making a decision, never re-executing.
"""

import importlib.util
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

import agent_core
import failure_injector as fi
import harness
import orchestrator
import state_manager as sm
from conftest import FIXES, REPO_ROOT, error_response, plan_response

FIX = {"tool": FIXES["SCHEMA_DRIFT"][0], "params": FIXES["SCHEMA_DRIFT"][1]}
WRONG = {"tool": "add_column", "params": {"table": "pipeline_silver", "column": "amount_usd", "column_type": "REAL"}}


@pytest.fixture
def executions(monkeypatch):
    """Count calls to the pipeline-write primitive."""
    calls = []
    original = harness.pipeline_write.write
    monkeypatch.setattr(harness.pipeline_write, "write", lambda conn, tool, params: (calls.append(tool), original(conn, tool, params))[1])
    return calls


def _new_run(scenario="SCHEMA_DRIFT"):
    fi.inject(scenario, 42)
    run_id = sm.start_run(scenario)
    sm.checkpoint(run_id, "run_started", {})
    return run_id


def _planned_attempt(run_id, action=FIX):
    attempt_id = sm.start_attempt(run_id)
    sm.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "plan": json.dumps({"diagnosis": "d", "reasoning": "r", "action": action})})
    return attempt_id


def _allow(run_id, attempt_id):
    used = sm.resume(run_id)["attempts_used"]
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": used + 1})


def _valid(run_id, attempt_id):
    sm.checkpoint(run_id, "tool_validation", {"attempt_id": attempt_id, "tool_validation_result": "VALID"})


def _events(env, run_id):
    if not env["trace"].exists():
        return []
    return [e for e in map(json.loads, env["trace"].read_text(encoding="utf-8").splitlines()) if e["scenario_run_id"] == run_id]


# --- harness.resume_attempt: continue from each persisted stage (INV-S4) -------------

def test_resume_with_only_a_plan_runs_the_whole_funnel(env, executions):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id)
    outcome = harness.resume_attempt(run_id, attempt_id, FIX)
    assert outcome.verification_result == "PASS" and executions == ["rename_column"]
    assert sm.resume(run_id)["attempts_used"] == 1


@pytest.mark.parametrize("decision", ["DENY", "REQUIRE_APPROVAL"])
def test_resume_after_a_block_does_not_re_decide_or_execute(env, executions, decision):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id)
    sm.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": decision})
    outcome = harness.resume_attempt(run_id, attempt_id, FIX)  # even a now-allowed action is not re-evaluated
    assert outcome == harness.AttemptOutcome(decision) and executions == []
    assert sm.resume(run_id)["attempts_used"] == 0


def test_resume_after_allow_does_not_increment_again(env, executions):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id)
    _allow(run_id, attempt_id)
    outcome = harness.resume_attempt(run_id, attempt_id, FIX)
    assert outcome.verification_result == "PASS" and executions == ["rename_column"]
    assert sm.resume(run_id)["attempts_used"] == 1  # INV-D1: the in-flight attempt keeps its single unit


def test_resume_after_rejection_asks_for_a_replan(env, executions):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id)
    _allow(run_id, attempt_id)
    sm.checkpoint(run_id, "tool_validation", {"attempt_id": attempt_id, "tool_validation_result": "REJECTED",
                                              "failure_reason": "bad params"})
    outcome = harness.resume_attempt(run_id, attempt_id, FIX)
    assert outcome.needs_replan and outcome.failure_reason == "bad params" and executions == []


def test_case_1_not_applied_executes_exactly_once(env, executions):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id)
    _allow(run_id, attempt_id)
    _valid(run_id, attempt_id)
    assert sm.resume(run_id)["action_applied"] is False
    outcome = harness.resume_attempt(run_id, attempt_id, FIX)
    assert executions == ["rename_column"] and outcome.verification_result == "PASS"


def test_case_1_after_pre_execute_executes_exactly_once(env, executions, monkeypatch):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id)
    _allow(run_id, attempt_id)
    _valid(run_id, attempt_id)

    def crash_inside(conn):
        conn.execute('ALTER TABLE "pipeline_silver" RENAME COLUMN "amt" TO "amount"')
        raise KeyboardInterrupt("simulated kill before commit")  # the transaction rolls back
    with pytest.raises(KeyboardInterrupt):
        sm.execute_and_checkpoint(run_id, attempt_id, crash_inside)
    state = sm.resume(run_id)
    assert state["last_stage"] == "pre_execute" and state["action_applied"] is False
    outcome = harness.resume_attempt(run_id, attempt_id, FIX)
    assert executions == ["rename_column"] and outcome.verification_result == "PASS"


def test_case_2_applied_goes_straight_to_verification(env, executions, monkeypatch):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id)
    _allow(run_id, attempt_id)
    _valid(run_id, attempt_id)
    monkeypatch.setattr(harness, "_verify", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt("killed after commit")))
    with pytest.raises(KeyboardInterrupt):
        harness.resume_attempt(run_id, attempt_id, FIX)
    monkeypatch.undo()
    executions.clear()
    original = harness.pipeline_write.write
    monkeypatch.setattr(harness.pipeline_write, "write", lambda *a: (executions.append("again"), original(*a))[1])
    assert sm.resume(run_id)["action_applied"] is True
    outcome = harness.resume_attempt(run_id, attempt_id, FIX)
    assert executions == []  # never re-executed (INV-S4)
    assert outcome.executed and outcome.verification_result == "PASS"
    assert outcome.execution_result == "rename_column: renamed pipeline_silver.amt to amount"


def test_resume_of_a_finished_attempt_changes_nothing(env, executions):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id)
    first = harness.attempt_action(run_id, attempt_id, FIX)
    before = fi.pipeline_state()
    executions.clear()
    assert harness.resume_attempt(run_id, attempt_id, FIX) == first
    assert executions == [] and fi.pipeline_state() == before


def test_only_the_latest_attempt_can_be_resumed(env):
    run_id = _new_run()
    older = _planned_attempt(run_id)
    _planned_attempt(run_id)
    with pytest.raises(ValueError, match="not the latest attempt"):
        harness.resume_attempt(run_id, older, FIX)


# --- orchestrator.resume_run ------------------------------------------------------

def test_resume_traces_what_it_found(env, scripted_api):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id)
    _allow(run_id, attempt_id)
    _valid(run_id, attempt_id)
    result = orchestrator.resume_run(run_id, client=scripted_api["client"])
    assert result.status == "RECOVERED" and scripted_api["requests"] == []  # finished the in-flight attempt; no new plan
    resume = [e for e in _events(env, run_id) if e["payload"].get("stage") == "resume"]
    assert [e["payload"] for e in resume] == [{"stage": "resume", "last_stage": "tool_validation", "action_applied": False,
                                               "attempt_id": attempt_id, "attempts_used": 1}]
    assert resume[0]["event_type"] == "state_transition"


def test_resume_of_a_terminal_run_is_a_no_op(env, scripted_api):
    scripted_api["script"] = [plan_response(*FIXES["SCHEMA_DRIFT"])]
    done = orchestrator.run_scenario("SCHEMA_DRIFT", 42, client=scripted_api["client"])
    lines_before = env["trace"].read_text(encoding="utf-8")
    result = orchestrator.resume_run(done.scenario_run_id, client=scripted_api["client"])
    assert result.status == "RECOVERED" and result.reason_code == "ALREADY_TERMINAL"
    assert env["trace"].read_text(encoding="utf-8") == lines_before


def test_resume_before_any_plan_plans_afresh_without_reinjecting(env, scripted_api, monkeypatch):
    run_id = _new_run()
    monkeypatch.setattr(fi, "inject", lambda *a: pytest.fail("resume must never re-inject"))
    scripted_api["script"] = [plan_response(*FIXES["SCHEMA_DRIFT"])]
    result = orchestrator.resume_run(run_id, client=scripted_api["client"])
    assert result.status == "RECOVERED" and len(scripted_api["requests"]) == 1


def test_resume_after_an_empty_attempt_row_starts_a_new_attempt(env, scripted_api):
    run_id = _new_run()
    sm.start_attempt(run_id)  # crash between start_attempt and the plan checkpoint
    scripted_api["script"] = [plan_response(*FIXES["SCHEMA_DRIFT"])]
    result = orchestrator.resume_run(run_id, client=scripted_api["client"])
    assert result.status == "RECOVERED" and sm.resume(run_id)["attempts_used"] == 1


def test_resume_after_a_failed_attempt_continues_the_loop(env, scripted_api):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id, WRONG)
    harness.attempt_action(run_id, attempt_id, WRONG)  # verification FAIL, then the process died
    scripted_api["script"] = [plan_response(*FIXES["SCHEMA_DRIFT"])]
    result = orchestrator.resume_run(run_id, client=scripted_api["client"])
    assert result.status == "RECOVERED" and sm.resume(run_id)["attempts_used"] == 2


def test_resume_respects_the_spent_budget(env, scripted_api):
    run_id = _new_run()
    for _ in range(3):
        attempt_id = _planned_attempt(run_id, WRONG if _ == 0 else {"tool": "backfill_column", "params": {
            "table": "pipeline_silver", "column": "customer", "value": "x"}})
        harness.attempt_action(run_id, attempt_id, json.loads(sm.resume(run_id)["attempt"]["plan"])["action"])
    result = orchestrator.resume_run(run_id, client=scripted_api["client"])
    assert result.reason_code == "BUDGET_EXHAUSTED" and scripted_api["requests"] == []
    assert sm.resume(run_id)["attempts_used"] == 3


def test_resume_error_completes_the_run(env, scripted_api):
    run_id = _new_run()
    scripted_api["script"] = [error_response(401, "authentication_error")]
    result = orchestrator.resume_run(run_id, client=scripted_api["client"])
    assert result.reason_code == "INFRASTRUCTURE_FAILURE" and sm.resume(run_id)["status"] == "UNRECOVERED"


# --- scripts/resume_scenario.py ----------------------------------------------------

def _load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), REPO_ROOT / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resume_cli_resumes_and_reports(env, capsys):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id)
    _allow(run_id, attempt_id)
    _valid(run_id, attempt_id)
    status = _load_script("resume_scenario.py").main(["--scenario-run-id", str(run_id), "--db", str(env["db"]),
                                                      "--trace", str(env["trace"])])
    out = capsys.readouterr().out
    assert status == 0 and f"ScenarioRun {run_id} (SCHEMA_DRIFT) after resume: RECOVERED" in out
    assert "Trace segment:" in out


def test_resume_cli_rejects_a_missing_database(tmp_path, capsys):
    status = _load_script("resume_scenario.py").main(["--scenario-run-id", "1", "--db", str(tmp_path / "none.db")])
    assert status == 2 and not (tmp_path / "none.db").exists()


def test_resume_cli_help():
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "resume_scenario.py"), "--help"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and "--scenario-run-id" in result.stdout


def test_trace_segment_skips_partial_lines(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"scenario_run_id": 1, "payload": {}}\n{"scenario_run_id": 1, "payl\n[1, 2]\n'
                     '{"scenario_run_id": 2, "payload": {}}\n{"scenario_run_id": 1, "payload": {"n": 2}}\n', encoding="utf-8")
    segment = _load_script("run_scenario.py").write_trace_segment(trace, 1)
    assert [json.loads(line)["payload"] for line in segment.read_text(encoding="utf-8").splitlines()] == [{}, {"n": 2}]


# --- Real process kills at every point (scripts/simulate_crash_resume.py) --------------

def test_crash_simulation_passes_every_assertion():
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "simulate_crash_resume.py"), "--assert-atomic",
                             "--assert-both-cases", "--assert-idempotent", "--assert-all-three-cases"],
                            capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr[-2000:]
    report = {r["kill_point"]: r for r in json.loads(result.stdout)}
    for mid_transaction in ("mid_apply", "post_execute_uncommitted"):
        assert report[mid_transaction]["write_lock_held_at_kill"]  # the kill provably landed mid-transaction
    # Challenge Finding 6: post_execute written but not committed reads as NOT applied.
    assert report["post_execute_uncommitted"]["after_kill"]["action_applied"] is False
    assert report["post_execute_uncommitted"]["after_kill"]["pipeline_mutated"] is False
    assert [report[k]["after_resume"]["executions_during_resume"] for k in
            ("before_pre_execute", "mid_apply", "post_execute_uncommitted", "after_commit",
             "after_commit_before_trace")] == [1, 1, 1, 0, 0]
    assert report["after_commit_before_trace"]["after_resume"]["tool_call_events"] == 0  # the resume event is the record
    assert report["after_commit_before_trace"]["after_resume"]["resume_events"][0]["action_applied"] is True



def _die(*args, **kwargs):
    raise KeyboardInterrupt("simulated kill")


# --- Challenge Finding 1: action_applied is scoped to the latest attempt (INV-S3/S4) --------

def test_earlier_applied_attempt_does_not_mark_the_next_as_applied(env, executions):
    run_id = _new_run()
    first = _planned_attempt(run_id, WRONG)
    assert harness.attempt_action(run_id, first, WRONG).verification_result == "FAIL"  # applied, then failed
    second = _planned_attempt(run_id)
    _allow(run_id, second)
    _valid(run_id, second)  # killed before its pre_execute
    assert sm.resume(run_id)["action_applied"] is False  # the latest attempt, not the run
    executions.clear()
    outcome = harness.resume_attempt(run_id, second, FIX)
    assert executions == ["rename_column"] and outcome.verification_result == "PASS"


# --- Challenge Finding 2: a crash during resume, then a second resume (INV-S4 detection) ----

def test_resume_twice_after_a_crash_during_resume(env, scripted_api, executions, monkeypatch):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id)
    _allow(run_id, attempt_id)
    _valid(run_id, attempt_id)
    real_verify = harness._verify
    monkeypatch.setattr(harness, "_verify", _die)
    with pytest.raises(KeyboardInterrupt):
        orchestrator.resume_run(run_id, client=scripted_api["client"])  # executes, then dies before verification
    monkeypatch.setattr(harness, "_verify", real_verify)
    after_first = fi.pipeline_state()
    assert executions == ["rename_column"] and sm.resume(run_id)["action_applied"] is True
    result = orchestrator.resume_run(run_id, client=scripted_api["client"])
    assert result.status == "RECOVERED"
    assert executions == ["rename_column"]  # the second resume did not execute again
    assert fi.pipeline_state() == after_first  # pipeline unchanged by the second resume
    assert sm.resume(run_id)["attempts_used"] == 1
    assert len([e for e in _events(env, run_id) if e["payload"].get("stage") == "resume"]) == 2


# --- Challenge Finding 3: verification FAIL on resume hands over to the re-plan loop ---------

@pytest.mark.parametrize("applied_before_crash", [False, True])
def test_resume_verification_fail_replans(env, scripted_api, monkeypatch, applied_before_crash):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id, WRONG)
    _allow(run_id, attempt_id)
    _valid(run_id, attempt_id)
    if applied_before_crash:  # case 2: committed, killed before verification
        real_verify = harness._verify
        monkeypatch.setattr(harness, "_verify", _die)
        with pytest.raises(KeyboardInterrupt):
            harness.resume_attempt(run_id, attempt_id, WRONG)
        monkeypatch.setattr(harness, "_verify", real_verify)
    scripted_api["script"] = [plan_response(*FIXES["SCHEMA_DRIFT"])]
    result = orchestrator.resume_run(run_id, client=scripted_api["client"])
    assert result.status == "RECOVERED" and len(scripted_api["requests"]) == 1
    assert sm.resume(run_id)["attempts_used"] == 2


# --- Challenge Finding 4: in-flight attempt 3 fails verification on resume ------------------

def test_in_flight_last_attempt_failing_on_resume_exhausts_the_budget(env, scripted_api, monkeypatch):
    run_id = _new_run()
    for value in ("x", "y"):
        action = {"tool": "backfill_column", "params": {"table": "pipeline_silver", "column": "customer", "value": value}}
        harness.attempt_action(run_id, _planned_attempt(run_id, action), action)
    third = _planned_attempt(run_id, WRONG)
    _allow(run_id, third)
    _valid(run_id, third)
    real_verify = harness._verify
    monkeypatch.setattr(harness, "_verify", _die)
    with pytest.raises(KeyboardInterrupt):
        harness.resume_attempt(run_id, third, WRONG)  # attempt 3 commits, then the process dies
    monkeypatch.setattr(harness, "_verify", real_verify)
    result = orchestrator.resume_run(run_id, client=scripted_api["client"])
    assert result.reason_code == "BUDGET_EXHAUSTED"
    assert scripted_api["requests"] == []  # no planning call once the budget is spent
    assert sm.resume(run_id)["attempts_used"] == 3


def test_resume_of_a_rejected_in_flight_attempt_replans(env, scripted_api):
    run_id = _new_run()
    attempt_id = _planned_attempt(run_id)
    _allow(run_id, attempt_id)
    sm.checkpoint(run_id, "tool_validation", {"attempt_id": attempt_id, "tool_validation_result": "REJECTED",
                                              "failure_reason": "bad params"})
    scripted_api["script"] = [plan_response(*FIXES["SCHEMA_DRIFT"])]
    result = orchestrator.resume_run(run_id, client=scripted_api["client"])
    assert result.status == "RECOVERED" and sm.resume(run_id)["attempts_used"] == 2

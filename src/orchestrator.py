"""src/orchestrator.py — run one scenario end to end, with a bounded recovery loop (Tasks 3.3, 4.1).

run_scenario(scenario_type, seed):
  (1) creates the ScenarioRun (checkpointed run_started),
  (2) injects the failure through ablation_fixture.get_seed_state — the single source of seeded
      state shared with the naive baseline (INV-D6),
  (3) loops: the agent proposes a plan (agent_core.diagnose_and_plan), the proposal goes through
      harness.attempt_action — never around it (INV-S1: no reference here to the pipeline-write
      primitive or execute_and_checkpoint) — until the run is recovered or must stop,
  (4) completes the run: RECOVERED only when an attempt's Deterministic Verification passed
      (the State Manager's INV-S5 guard enforces this too), otherwise UNRECOVERED with a reason code.

The recovery loop (Task 4.1):
  - Tool-validation REJECTED or verification FAIL → re-plan and retry. Both draw from the ONE
    shared budget, ScenarioRun.attempts_used <= max_attempts (3) (INV-D1); the funnel increments
    it once per ALLOW-decided attempt.
  - Policy DENY / REQUIRE_APPROVAL do not enter the loop and consume no budget (INV-D2): the run ends.
  - AgentAPIError is infrastructure, not a recovery attempt: the API call is retried with bounded
    exponential backoff (MAX_API_RETRIES), with no Attempt created and no budget used. If retries
    are exhausted: UNRECOVERED, reason INFRASTRUCTURE_FAILURE in the final trace event, and no
    Attempt.failure_reason is written (INV-D3).
  - Budget spent without a PASS → UNRECOVERED, reason BUDGET_EXHAUSTED, plus a final trace event.

A run always ends in a terminal status. If anything in the harness raises after the run exists, the
run is completed UNRECOVERED (HARNESS_ERROR) and the exception is re-raised.

Concurrency guard (Task 4.3, INV-S7): run_scenario creates its ScenarioRun exclusively — while
another run is IN_PROGRESS against the shared pipeline it raises state_manager.RunInProgressError
(naming that run) before anything is created or injected. Nothing is queued or overwritten; an
interrupted run must be resumed (resume_run) or it keeps the pipeline locked.

resume_run(scenario_run_id) (Task 4.2) continues a run interrupted by a crash, from exactly its last
checkpoint (INV-S4): it traces what it found (last_stage, action_applied), finishes the in-flight
attempt through harness.resume_attempt — which never re-executes an applied action — and then
continues the same recovery loop. It never re-injects the failure: the pipeline is as the crash left it.
"""

import json
import sqlite3
import time
from dataclasses import dataclass

import ablation_fixture
import agent_core
import failure_injector
import harness
import scenario_expectations  # noqa: F401 — registers Verification's expectations in every harnessed process
import state_manager
import trace_logger

RECOVERED = "RECOVERED"
UNRECOVERED = "UNRECOVERED"
MAX_API_RETRIES = 3
API_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
_sleep = time.sleep  # replaceable in tests


@dataclass(frozen=True)
class ScenarioResult:
    """The outcome of one scenario run."""

    scenario_run_id: int
    scenario_type: str
    seed: int
    status: str
    reason: str  # "<CODE>: <detail>"
    plan: object = None  # the last agent_core.Plan, if any
    outcome: object = None  # the last harness.AttemptOutcome, if any
    initial_state_hash: str = None  # SHA-256 of the pipeline right after injection (INV-D6)

    @property
    def reason_code(self) -> str:
        return self.reason.split(":", 1)[0]


@dataclass(frozen=True)
class _Ending:
    """How the recovery loop ended; attempt_id is set only for RECOVERED (the verified attempt)."""

    status: str
    code: str
    detail: str
    attempt_id: int = None
    plan: object = None
    outcome: object = None


def init(db_path, trace_path) -> None:
    """Point every component at one database and one trace file."""
    harness.init(db_path, trace_path)
    failure_injector.init(db_path)
    agent_core.init(db_path)


def run_scenario(scenario_type: str, seed: int, client=None, parity_with=None) -> ScenarioResult:
    """Inject scenario_type's failure, then plan and attempt fixes until recovered or stopped.
    With parity_with (the naive side's SeedState), the initial states are compared before the agent
    is asked for anything; a mismatch raises ablation_fixture.AblationIntegrityError (INV-D6)."""
    run_id = state_manager.start_run(scenario_type, exclusive=True)  # INV-S7: RunInProgressError if one is live
    try:
        seed_state = ablation_fixture.get_seed_state(scenario_type, seed)  # the single source (INV-D6)
        if parity_with is not None:
            ablation_fixture.require_parity(parity_with, seed_state)
        state_manager.checkpoint(run_id, "run_started", {})
        trace_logger.emit(run_id, None, "state_transition",
                          {"stage": "run_started", "scenario_type": scenario_type, "seed": seed,
                           "injected": seed_state.description, "initial_state_hash": seed_state.state_hash})
        result = _finish(run_id, scenario_type, seed, _recovery_loop(run_id, client))
        return ScenarioResult(**{**result.__dict__, "initial_state_hash": seed_state.state_hash})
    except Exception as exc:
        _abandon(run_id, exc)
        raise


def resume_run(scenario_run_id: int, client=None) -> ScenarioResult:
    """Continue an interrupted run from its persisted state; a terminal run is reported unchanged."""
    state = state_manager.resume(scenario_run_id)
    if state["status"] != "IN_PROGRESS":
        return ScenarioResult(scenario_run_id, state["scenario_type"], None, state["status"],
                              f"ALREADY_TERMINAL: run was {state['status']} before resume")
    try:
        trace_logger.emit(scenario_run_id, None, "state_transition", _resume_event(state))
        ending = _resume_in_flight_attempt(scenario_run_id, state) or _recovery_loop(scenario_run_id, client)
        return _finish(scenario_run_id, state["scenario_type"], None, ending)
    except Exception as exc:
        _abandon(scenario_run_id, exc)
        raise


def _resume_event(state: dict) -> dict:
    """The trace record of what resume() found — the evidence if a kill beat a trace line."""
    attempt = state["attempt"]
    return {"stage": "resume", "last_stage": state["last_stage"], "action_applied": state["action_applied"],
            "attempt_id": attempt["id"] if attempt else None, "attempts_used": state["attempts_used"]}


def _resume_in_flight_attempt(run_id: int, state: dict):
    """Finish the latest attempt if a plan was recorded for it; return its ending, or None to keep looping."""
    attempt = state["attempt"]
    if attempt is None or attempt["plan"] is None:
        return None  # nothing was proposed yet: plan afresh
    action = json.loads(attempt["plan"])["action"]
    try:
        outcome = harness.resume_attempt(run_id, attempt["id"], action)
    except sqlite3.OperationalError as exc:
        return _Ending(UNRECOVERED, "EXECUTION_ERROR", str(exc))
    return _ending_for(attempt["id"], None, outcome)


def _recovery_loop(run_id: int, client) -> _Ending:
    """Plan and attempt until an attempt passes verification or the run must stop (INV-D1, INV-D2)."""
    ending, last_failure = None, None
    while ending is None:
        ending, last_failure = _next_attempt(run_id, client, last_failure)
    return ending


def _next_attempt(run_id: int, client, last_failure) -> tuple:
    """Run one plan + funnel cycle. Return (ending or None to re-plan, description of this failure)."""
    state = state_manager.resume(run_id)
    if state["attempts_used"] >= state["max_attempts"]:
        detail = f"{state['attempts_used']} of {state['max_attempts']} attempts used without a verification PASS"
        return _Ending(UNRECOVERED, "BUDGET_EXHAUSTED", f"{detail}; last: {last_failure}"), last_failure
    try:
        plan = _plan_with_api_retries(run_id, client)
    except agent_core.AgentAPIError as exc:
        return _Ending(UNRECOVERED, "INFRASTRUCTURE_FAILURE", str(exc)), last_failure
    except agent_core.PlanningError as exc:
        return _Ending(UNRECOVERED, "PLANNING_FAILURE", str(exc)), last_failure
    attempt_id = state_manager.start_attempt(run_id)
    record = {"diagnosis": plan.diagnosis, "reasoning": plan.reasoning, "action": plan.action}
    state_manager.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "plan": json.dumps(record)})
    try:
        outcome = harness.attempt_action(run_id, attempt_id, plan.action)
    except sqlite3.OperationalError as exc:  # the validated action failed against the pipeline; rolled back
        return _Ending(UNRECOVERED, "EXECUTION_ERROR", str(exc), plan=plan), last_failure
    return _ending_for(attempt_id, plan, outcome), _failure_reason(outcome)


def _ending_for(attempt_id: int, plan, outcome):
    """RECOVERED on PASS; None (re-plan) on REJECTED or FAIL; a terminal ending on DENY / REQUIRE_APPROVAL.
    Any other outcome is a harness defect, never relabelled as a policy block (INV-D1, INV-D2)."""
    if outcome.verification_result == "PASS":
        return _Ending(RECOVERED, "VERIFIED", "verification passed", attempt_id, plan, outcome)
    if outcome.needs_replan or outcome.verification_result == "FAIL":
        return None
    if outcome.policy_decision in ("DENY", "REQUIRE_APPROVAL") and not outcome.executed:
        return _Ending(UNRECOVERED, f"POLICY_{outcome.policy_decision}", "the proposed action was not executed",
                       plan=plan, outcome=outcome)
    raise RuntimeError(f"unexpected funnel outcome for attempt {attempt_id}: {outcome!r}")


def _plan_with_api_retries(run_id: int, client):
    """Ask the agent for a plan, retrying API-level failures with backoff; no Attempt, no budget."""
    for retry in range(MAX_API_RETRIES + 1):
        try:
            return agent_core.diagnose_and_plan(run_id, client=client)
        except agent_core.AgentAPIError as exc:
            _wait_before_api_retry(run_id, exc, retry)


def _wait_before_api_retry(run_id: int, exc, retry: int) -> None:
    """Re-raise exc if it cannot or may no longer be retried; otherwise trace the retry and back off."""
    if not exc.retryable or retry >= MAX_API_RETRIES:
        raise exc
    trace_logger.emit(run_id, None, "state_transition",
                      {"stage": "plan", "api_retry": retry + 1, "max_api_retries": MAX_API_RETRIES, "error": str(exc)})
    _sleep(API_BACKOFF_SECONDS[retry])


def _failure_reason(outcome) -> str:
    """Why an attempt did not recover the scenario."""
    if outcome.policy_decision != "ALLOW":
        return f"POLICY_{outcome.policy_decision}"
    if outcome.tool_validation_result != "VALID":
        return f"TOOL_VALIDATION_REJECTED: {outcome.failure_reason}"
    return f"VERIFICATION_FAILED: {outcome.failure_reason}" if outcome.failure_reason else "VERIFIED"


def _finish(run_id: int, scenario_type: str, seed: int, ending: _Ending) -> ScenarioResult:
    """Checkpoint the terminal status (RECOVERED must cite the verified attempt), then trace it."""
    state = {"status": ending.status}
    if ending.attempt_id is not None:
        state["attempt_id"] = ending.attempt_id
    state_manager.checkpoint(run_id, "run_complete", state)
    trace_logger.emit(run_id, None, "state_transition",
                      {"stage": "run_complete", "status": ending.status, "reason": ending.code, "detail": ending.detail})
    return ScenarioResult(run_id, scenario_type, seed, ending.status, f"{ending.code}: {ending.detail}",
                          ending.plan, ending.outcome)


def _abandon(run_id: int, exc: Exception) -> None:
    """Complete a run UNRECOVERED after an unexpected harness error, unless it is already terminal."""
    try:
        if state_manager.resume(run_id)["status"] == "IN_PROGRESS":
            state_manager.checkpoint(run_id, "run_complete", {"status": UNRECOVERED})
            trace_logger.emit(run_id, None, "state_transition",
                              {"stage": "run_complete", "status": UNRECOVERED, "reason": "HARNESS_ERROR", "detail": repr(exc)})
    except Exception:  # the original error is the one worth raising
        pass

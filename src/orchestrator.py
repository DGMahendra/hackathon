"""src/orchestrator.py — run one scenario end to end (Task 3.3).

run_scenario(scenario_type, seed):
  (1) creates the ScenarioRun (checkpointed run_started),
  (2) injects the failure (failure_injector.inject),
  (3) asks the agent for a plan (agent_core.diagnose_and_plan),
  (4) routes the proposed action through harness.attempt_action — never around it (INV-S1:
      this module has no reference to the pipeline-write primitive or execute_and_checkpoint),
  (5) completes the run: RECOVERED only when the attempt's Deterministic Verification passed
      (the State Manager's INV-S5 guard enforces this too), otherwise UNRECOVERED with a reason.

Session 3 makes ONE attempt per run; the bounded, shared-budget retry loop is Session 4. A run
always ends in a terminal status, and every stage is in the trace (JSONL). If anything in the
harness raises after the run exists (a guard rejection, a database error), the run is completed
UNRECOVERED ("HARNESS_ERROR") and the exception is re-raised, so failures stay loud but no run
is left IN_PROGRESS.
"""

import json
import sqlite3
from dataclasses import dataclass

import agent_core
import failure_injector
import harness
import state_manager
import trace_logger

RECOVERED = "RECOVERED"
UNRECOVERED = "UNRECOVERED"


@dataclass(frozen=True)
class ScenarioResult:
    """The outcome of one scenario run."""

    scenario_run_id: int
    scenario_type: str
    seed: int
    status: str
    reason: str
    plan: object = None  # agent_core.Plan, if the agent produced one
    outcome: object = None  # harness.AttemptOutcome, if an action went through the funnel


def init(db_path, trace_path) -> None:
    """Point every component at one database and one trace file."""
    harness.init(db_path, trace_path)
    failure_injector.init(db_path)
    agent_core.init(db_path)


def run_scenario(scenario_type: str, seed: int, client=None) -> ScenarioResult:
    """Inject scenario_type's failure, plan a fix, route it through the funnel, complete the run."""
    run_id = state_manager.start_run(scenario_type)
    try:
        return _run(run_id, scenario_type, seed, client)
    except Exception as exc:
        _abandon(run_id, exc)
        raise


def _run(run_id: int, scenario_type: str, seed: int, client) -> ScenarioResult:
    """Inject, plan and attempt for an existing run; always completes the run on a planning outcome."""
    injection = failure_injector.inject(scenario_type, seed)
    state_manager.checkpoint(run_id, "run_started", {})
    trace_logger.emit(run_id, None, "state_transition",
                      {"stage": "run_started", "scenario_type": scenario_type, "seed": seed, "injected": injection.description})
    try:
        plan = agent_core.diagnose_and_plan(run_id, client=client)
    except agent_core.AgentAPIError as exc:
        return _complete(run_id, scenario_type, seed, None, UNRECOVERED, f"INFRASTRUCTURE_FAILURE: {exc}")
    except agent_core.PlanningError as exc:
        return _complete(run_id, scenario_type, seed, None, UNRECOVERED, f"PLANNING_FAILURE: {exc}")
    return _attempt_plan(run_id, scenario_type, seed, plan)


def _attempt_plan(run_id: int, scenario_type: str, seed: int, plan) -> ScenarioResult:
    """Record the plan on a new attempt, send its action through the funnel, then complete the run."""
    attempt_id = state_manager.start_attempt(run_id)
    record = {"diagnosis": plan.diagnosis, "reasoning": plan.reasoning, "action": plan.action}
    state_manager.checkpoint(run_id, "plan", {"attempt_id": attempt_id, "plan": json.dumps(record)})
    try:
        outcome = harness.attempt_action(run_id, attempt_id, plan.action)
    except sqlite3.OperationalError as exc:  # the validated action failed against the pipeline; rolled back
        return _complete(run_id, scenario_type, seed, None, UNRECOVERED, f"EXECUTION_ERROR: {exc}", plan)
    passed = outcome.verification_result == "PASS"
    status, reason = (RECOVERED, "verification passed") if passed else (UNRECOVERED, _failure_reason(outcome))
    return _complete(run_id, scenario_type, seed, attempt_id if passed else None, status, reason, plan, outcome)


def _abandon(run_id: int, exc: Exception) -> None:
    """Complete a run UNRECOVERED after an unexpected harness error, unless it is already terminal."""
    try:
        if state_manager.resume(run_id)["status"] == "IN_PROGRESS":
            state_manager.checkpoint(run_id, "run_complete", {"status": UNRECOVERED})
            trace_logger.emit(run_id, None, "state_transition",
                              {"stage": "run_complete", "status": UNRECOVERED, "reason": f"HARNESS_ERROR: {exc!r}"})
    except Exception:  # the original error is the one worth raising
        pass


def _failure_reason(outcome) -> str:
    """Why an attempt did not recover the scenario."""
    if outcome.policy_decision != "ALLOW":
        return f"POLICY_{outcome.policy_decision}: the proposed action was not executed"
    if outcome.tool_validation_result != "VALID":
        return f"TOOL_VALIDATION_REJECTED: {outcome.failure_reason}"
    return f"VERIFICATION_FAILED: {outcome.failure_reason}"


def _complete(run_id, scenario_type, seed, attempt_id, status, reason, plan=None, outcome=None) -> ScenarioResult:
    """Checkpoint the terminal status (RECOVERED must cite the verified attempt), then trace it."""
    state = {"status": status} if attempt_id is None else {"attempt_id": attempt_id, "status": status}
    state_manager.checkpoint(run_id, "run_complete", state)
    trace_logger.emit(run_id, None, "state_transition", {"stage": "run_complete", "status": status, "reason": reason})
    return ScenarioResult(run_id, scenario_type, seed, status, reason, plan, outcome)

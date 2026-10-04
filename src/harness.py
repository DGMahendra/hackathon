"""src/harness.py — the Execute funnel function (Task 2.4).

attempt_action(scenario_run_id, attempt_id, action) is the ONLY legal call path to applying
an action to the pipeline (INV-S1). In fixed order:

  (1) Policy: policy_layer.evaluate(action). DENY or REQUIRE_APPROVAL → checkpoint the
      decision (execution_result stays NULL), trace it, return — no execution and no
      attempts_used increment (INV-S2, INV-D2). REQUIRE_APPROVAL goes to the approval stub
      (PENDING). ALLOW → checkpoint the decision together with attempts_used + 1 (INV-D2).
  (2) Tool Validation: tool_validation.validate(action), checkpointed. REJECTED → no
      execution; the outcome asks for a re-plan (the bounded loop is Session 4).
  (3) Execute: state_manager.execute_and_checkpoint() with an apply_fn that calls the
      pipeline-write primitive — pre_execute committed alone, then the mutation and
      post_execute atomically (INV-S3); writes confined to PipelineState (INV-S8).
  (4) Verify: verification.verify(scenario_run_id); its result is checkpointed (INV-S5).

Every trace event is emitted only AFTER the checkpoint it describes has committed, so the
trace never claims something that did not commit (ARCHITECTURE.md §8). This module never
sets the run's status — completing a run is the orchestrator's job (Sessions 3–4).

resume_attempt(scenario_run_id, attempt_id, action) continues an interrupted attempt from its
persisted stage (Task 4.2, INV-S4): a recorded gate decision is never re-made, and an action
whose post_execute checkpoint exists is never re-executed. Exactly two execute cases exist,
because execute_and_checkpoint() is atomic (INV-S3): action_applied False → execute (once);
action_applied True → straight to verification. Both reuse the same stage functions as
attempt_action, so `_execute` stays the single call site of execute_and_checkpoint (INV-S1).
"""

import json
from dataclasses import dataclass

import pipeline_write
import policy_layer
import state_manager
import tool_validation
import trace_logger
import verification
from policy_layer import PolicyDecision


@dataclass(frozen=True)
class AttemptOutcome:
    """What happened to one action in the funnel."""

    policy_decision: str
    tool_validation_result: str = None
    executed: bool = False
    execution_result: str = None
    verification_result: str = None
    failure_reason: str = None
    needs_replan: bool = False


def init(db_path, trace_path) -> None:
    """Point the State Manager, Trace Logger and Verification at one database and trace file."""
    state_manager.init(db_path)
    trace_logger.init(db_path, trace_path)
    verification.init(db_path)


def attempt_action(scenario_run_id: int, attempt_id: int, action) -> AttemptOutcome:
    """Route action through Policy → Tool Validation → Execute → Verify, checkpointing each stage."""
    decision = policy_layer.evaluate(action)
    if decision is not PolicyDecision.ALLOW:
        return _record_blocked(scenario_run_id, attempt_id, decision, action)
    _record_allow(scenario_run_id, attempt_id, action)
    return _validate_execute_verify(scenario_run_id, attempt_id, action)


def resume_attempt(scenario_run_id: int, attempt_id: int, action) -> AttemptOutcome:
    """Continue the run's latest attempt from its persisted stage; never redo a recorded step (INV-S4)."""
    state = state_manager.resume(scenario_run_id)
    attempt = state["attempt"]
    if attempt is None or attempt["id"] != attempt_id:
        raise ValueError(f"attempt {attempt_id} is not the latest attempt of scenario_run {scenario_run_id}")
    decision, validation = attempt["policy_decision"], attempt["tool_validation_result"]
    if decision is None:
        return attempt_action(scenario_run_id, attempt_id, action)
    if decision != PolicyDecision.ALLOW.value:
        return AttemptOutcome(decision)
    if validation is None:
        return _validate_execute_verify(scenario_run_id, attempt_id, action)
    if validation != tool_validation.VALID:
        return AttemptOutcome(decision, validation, failure_reason=attempt["failure_reason"], needs_replan=True)
    if attempt["verification_result"] is not None:
        return AttemptOutcome(decision, validation, True, attempt["execution_result"],
                              attempt["verification_result"], attempt["failure_reason"])
    if not state["action_applied"]:
        return _execute_and_verify(scenario_run_id, attempt_id, action)  # case 1: never committed → execute once
    return _verify(scenario_run_id, attempt_id, attempt["execution_result"])  # case 2: committed → verify only


def _validate_execute_verify(scenario_run_id: int, attempt_id: int, action) -> AttemptOutcome:
    """Stages (2)–(4) for an ALLOW-decided attempt: validate, then execute and verify if VALID."""
    validation = tool_validation.validate(action)
    _record_validation(scenario_run_id, attempt_id, validation)
    if not validation.is_valid:
        return AttemptOutcome(PolicyDecision.ALLOW.value, validation.status, failure_reason=validation.reason,
                              needs_replan=True)
    return _execute_and_verify(scenario_run_id, attempt_id, action)


def _execute_and_verify(scenario_run_id: int, attempt_id: int, action) -> AttemptOutcome:
    """Stages (3)–(4): apply the action atomically with post_execute, then verify."""
    return _verify(scenario_run_id, attempt_id, _execute(scenario_run_id, attempt_id, action))


def _verify(scenario_run_id: int, attempt_id: int, execution_result: str) -> AttemptOutcome:
    """Stage (4): Deterministic Verification of an applied attempt, checkpointed (INV-S5)."""
    result = verification.verify(scenario_run_id)
    _record_verification(scenario_run_id, attempt_id, result)
    return AttemptOutcome(PolicyDecision.ALLOW.value, tool_validation.VALID, True, execution_result,
                          result.status, result.failure_reason)


def _record_blocked(scenario_run_id: int, attempt_id: int, decision: PolicyDecision, action) -> AttemptOutcome:
    """Checkpoint a DENY / REQUIRE_APPROVAL decision and trace it; nothing executes (INV-S2, INV-D2)."""
    state_manager.checkpoint(scenario_run_id, "policy", {"attempt_id": attempt_id, "policy_decision": decision.value})
    payload = {"decision": decision.value, "action": _describe(action)}
    if decision is PolicyDecision.REQUIRE_APPROVAL:
        payload["approval"] = policy_layer.request_approval(action)
    trace_logger.emit(scenario_run_id, attempt_id, "policy_decision", payload)
    return AttemptOutcome(decision.value)


def _record_allow(scenario_run_id: int, attempt_id: int, action) -> None:
    """Checkpoint ALLOW with attempts_used + 1 in the same transaction (INV-D2), then trace it."""
    used = state_manager.resume(scenario_run_id)["attempts_used"]
    state_manager.checkpoint(
        scenario_run_id, "policy",
        {"attempt_id": attempt_id, "policy_decision": PolicyDecision.ALLOW.value, "attempts_used": used + 1},
    )
    trace_logger.emit(scenario_run_id, attempt_id, "policy_decision",
                      {"decision": PolicyDecision.ALLOW.value, "action": _describe(action), "attempts_used": used + 1})


def _record_validation(scenario_run_id: int, attempt_id: int, validation) -> None:
    """Checkpoint the Tool Validation result (failure_reason iff REJECTED, INV-D3), then trace it."""
    state = {"attempt_id": attempt_id, "tool_validation_result": validation.status}
    if not validation.is_valid:
        state["failure_reason"] = validation.reason
    state_manager.checkpoint(scenario_run_id, "tool_validation", state)
    trace_logger.emit(scenario_run_id, attempt_id, "state_transition",
                      {"stage": "tool_validation", "result": validation.status, "reason": validation.reason})


def _execute(scenario_run_id: int, attempt_id: int, action) -> str:
    """Apply the validated action through the pipeline-write primitive, atomically with post_execute."""
    tool, params = action["tool"], action["params"]

    def apply_fn(conn):
        return pipeline_write.write(conn, tool, params)

    execution_result = state_manager.execute_and_checkpoint(scenario_run_id, attempt_id, apply_fn)
    trace_logger.emit(scenario_run_id, attempt_id, "tool_call",
                      {"tool": tool, "params": params, "execution_result": execution_result})
    return execution_result


def _record_verification(scenario_run_id: int, attempt_id: int, result) -> None:
    """Checkpoint verify()'s result — the only source of verification_result (INV-S5) — then trace it."""
    state = {"attempt_id": attempt_id, "verification_result": result.status}
    if result.failure_reason is not None:
        state["failure_reason"] = result.failure_reason
    state_manager.checkpoint(scenario_run_id, "verification", state)
    trace_logger.emit(scenario_run_id, attempt_id, "state_transition",
                      {"stage": "verification", "result": result.status, "details": list(result.details)})


def _describe(action):
    """Return the action as recorded in the trace: as-is if it is strict JSON, else its repr."""
    try:
        json.dumps(action, allow_nan=False)
    except (TypeError, ValueError):
        return repr(action)
    return action

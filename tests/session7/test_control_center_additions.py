"""tests/session7/test_control_center_additions.py — the additive changes made for the control center.

Every change is opt-in; these tests pin both halves: the new behaviour when asked for, and the ORIGINAL
behaviour when not (default approver is PENDING, value checks are off, the policy decides as before).
Plain functions with no fixtures, so they run under pytest or by being called directly.
"""

import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for sub in ("src", "scripts"):
    if str(REPO_ROOT / sub) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / sub))

import failure_injector  # noqa: E402
import harness  # noqa: E402
import init_db  # noqa: E402
import policy_layer  # noqa: E402
import scenario_expectations  # noqa: E402
import state_manager  # noqa: E402
import verification  # noqa: E402

BACKFILL_REGION = {"tool": "backfill_column", "params": {"table": "pipeline_silver", "column": "region", "value": "APAC"}}


def _env(variant="NULL_ONLY", scenario="PROMPT_INJECTION"):
    """A throwaway database and trace with the scenario injected; returns (db, trace, run_id, attempt_id)."""
    workdir = Path(tempfile.mkdtemp(prefix="dataops-s7-"))
    db, trace = workdir / "h.db", workdir / "t.jsonl"
    init_db.create_database(db)
    harness.init(db, trace)
    failure_injector.init(db)
    failure_injector.inject(scenario, 42, variant=variant)
    run = state_manager.start_run(scenario)
    return db, trace, run, state_manager.start_attempt(run)


def _events(trace):
    return [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.startswith("{")]


def _reset():
    policy_layer.set_approver(None)
    policy_layer.set_approval_required(())
    verification.clear_value_checks()


def test_explain_decision_equals_evaluate():
    actions = [BACKFILL_REGION, {"tool": "drop_column", "params": {"table": "pipeline_silver", "column": "x"}},
               {"tool": "upload_record", "params": {"url": "https://x"}}, None, {"tool": 1, "params": {}}, {"tool": "add_column"}]
    for action in actions:
        explanation = policy_layer.explain(action)
        assert explanation.decision is policy_layer.evaluate(action)
        assert explanation.reason and len(explanation.checks) == 5


def test_default_policy_and_approver_are_the_original_behaviour():
    _reset()
    assert policy_layer.evaluate(BACKFILL_REGION) is policy_layer.PolicyDecision.ALLOW
    drop = {"tool": "drop_column", "params": {"table": "pipeline_silver", "column": "x"}}
    assert policy_layer.request_approval(drop) == policy_layer.PENDING


def test_stricter_policy_only_tightens():
    try:
        policy_layer.set_approval_required(("backfill_column", "upload_record"))  # a non-allowlisted tool is ignored
        assert policy_layer.evaluate(BACKFILL_REGION) is policy_layer.PolicyDecision.REQUIRE_APPROVAL
        assert policy_layer.evaluate({"tool": "upload_record", "params": {"table": "pipeline_bronze"}}) is policy_layer.PolicyDecision.DENY
        bad_target = {"tool": "backfill_column", "params": {"table": "ScenarioRun", "column": "status", "value": "x"}}
        assert policy_layer.evaluate(bad_target) is policy_layer.PolicyDecision.DENY  # tightening never loosens a DENY
    finally:
        _reset()


def test_approved_action_goes_through_validation_execution_and_verification():
    db, trace, run, attempt = _env()
    try:
        policy_layer.set_approval_required(("backfill_column",))
        policy_layer.set_approver(lambda action: policy_layer.APPROVED)
        outcome = harness.attempt_action(run, attempt, BACKFILL_REGION)
        assert (outcome.policy_decision, outcome.tool_validation_result, outcome.executed, outcome.verification_result) == ("ALLOW", "VALID", True, "PASS")
        names = [(e["event_type"], e["payload"].get("stage") or e["payload"].get("decision")) for e in _events(trace)]
        assert ("state_transition", "approval") in names and ("policy_decision", "ALLOW") in names and ("tool_call", None) in names
        assert any(e["payload"].get("approved_by") == "human" for e in _events(trace))
        assert state_manager.resume(run)["attempts_used"] == 1  # an approved ALLOW consumes budget like any ALLOW (INV-D2)
    finally:
        _reset()


def test_rejected_or_unanswered_approval_executes_nothing():
    for answer, expected in ((policy_layer.REJECTED, "REJECTED"), (policy_layer.PENDING, "PENDING"), ("maybe", "PENDING")):
        db, trace, run, attempt = _env()
        try:
            policy_layer.set_approval_required(("backfill_column",))
            policy_layer.set_approver(lambda action, a=answer: a)
            outcome = harness.attempt_action(run, attempt, BACKFILL_REGION)
            assert outcome == harness.AttemptOutcome("REQUIRE_APPROVAL")
            assert not any(e["event_type"] == "tool_call" for e in _events(trace))
            decision = [e["payload"] for e in _events(trace) if e["event_type"] == "policy_decision"][0]
            assert decision["approval"] == expected
            assert state_manager.resume(run)["attempts_used"] == 0  # INV-D2
            with closing(sqlite3.connect(db)) as conn:
                assert conn.execute("SELECT COUNT(*) FROM pipeline_silver WHERE region IS NULL").fetchone()[0] == 2
        finally:
            _reset()


def test_null_only_variant_has_two_nulls_with_one_true_value_and_no_injected_text():
    db, trace, run, attempt = _env()
    with closing(sqlite3.connect(db)) as conn:
        silver = conn.execute("SELECT order_id FROM pipeline_silver WHERE region IS NULL ORDER BY order_id").fetchall()
        regions = {r[0] for r in conn.execute("SELECT region FROM pipeline_bronze WHERE order_id IN (%s)" % ",".join(str(o[0]) for o in silver))}
        records = [r[0] for r in conn.execute("SELECT record FROM pipeline_bronze")]
    assert len(silver) == 2 and len(regions) == 1
    assert all(record.startswith("order ") for record in records)  # no injected instruction text


def test_default_prompt_injection_fixture_is_unchanged():
    db, *_ = _env(variant=None)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pipeline_bronze WHERE record LIKE 'SYSTEM NOTICE%'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM pipeline_silver WHERE amount IS NULL").fetchone()[0] == 1


def test_value_checks_are_off_by_default_and_on_request():
    _reset()
    assert verification._value_checks == {}
    db, trace, run, attempt = _env()
    wrong = {"tool": "backfill_column", "params": {"table": "pipeline_silver", "column": "region", "value": "EU"}}
    try:
        verification.init(db)
        policy_layer.set_approval_required(())
        harness.attempt_action(run, attempt, wrong)  # allowed, valid, executed: a plausible but wrong fix
        assert verification.verify(run).status == "PASS"  # schema, rows, nulls cannot see it
        scenario_expectations.enable_value_checks()
        result = verification.verify(run)
        assert result.status == "FAIL" and any(d.startswith("value:") for d in result.details)
        groups = {g["name"]: g for g in verification.verify_detailed(run)}
        assert groups["schema"]["ok"] and groups["null_rate"]["ok"] and not groups["value"]["ok"]
    finally:
        _reset()


def test_verify_detailed_reports_the_same_failures_as_verify():
    db, trace, run, attempt = _env(variant=None, scenario="SCHEMA_DRIFT")  # unrepaired: amount is missing
    verification.init(db)
    flat = list(verification.verify(run).details)
    detailed = [f for g in verification.verify_detailed(run) for f in g["failures"]]
    assert flat == detailed and flat

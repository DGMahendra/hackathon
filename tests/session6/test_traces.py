"""tests/session6/test_traces.py — Task 6.4 trace artifacts (INV-D4 consumed)."""

import json

import pytest

from report_fixtures import REPO_ROOT, load_script

TRACES = REPO_ROOT / "docs" / "traces"
EVENT_FIELDS = {"timestamp", "scenario_run_id", "attempt_id", "event_type", "payload"}


def _events(name):
    return [json.loads(line) for line in (TRACES / name).read_text(encoding="utf-8").splitlines()]


# --- TC-1 / TC-2: valid JSONL, one run each, the expected final status ----------------------------

@pytest.mark.parametrize("name,status", [("success_trace.jsonl", "RECOVERED"), ("failure_trace.jsonl", "UNRECOVERED")])
def test_trace_is_one_complete_run_ending_in_status(name, status):
    events = _events(name)
    assert all(set(e) == EVENT_FIELDS for e in events)
    assert len({e["scenario_run_id"] for e in events}) == 1
    assert events[0]["payload"]["stage"] == "run_started"
    assert events[-1]["payload"]["stage"] == "run_complete" and events[-1]["payload"]["status"] == status


def test_success_trace_names_the_live_model():
    plans = [e["payload"] for e in _events("success_trace.jsonl") if e["payload"].get("stage") == "plan"]
    assert plans and all(p["model"] == "claude-sonnet-5" for p in plans)


# --- Challenge Finding 2: a SCHEMA_DRIFT run declared RECOVERED only after a Verification PASS (INV-S5) ---

def test_success_is_schema_drift_and_verified_before_recovered():
    events = _events("success_trace.jsonl")
    assert events[0]["payload"]["scenario_type"] == "SCHEMA_DRIFT"
    stages = [e["payload"].get("stage") for e in events]
    assert stages[-2:] == ["verification", "run_complete"] and events[-2]["payload"]["result"] == "PASS"


# --- Challenge Finding 4: every failed attempt went through the funnel in order (INV-S1) -------------

def test_failure_attempts_follow_the_funnel_order():
    events = [e for e in _events("failure_trace.jsonl") if e["attempt_id"] is not None]
    steps = [(e["event_type"], e["payload"].get("decision") or e["payload"].get("result") or e["payload"].get("tool"))
             for e in events]
    one_attempt = [("policy_decision", "ALLOW"), ("state_transition", "VALID"), ("tool_call", "backfill_column"),
                   ("state_transition", "FAIL")]
    assert steps == one_attempt * 3
    assert len({e["attempt_id"] for e in events}) == 3


def test_failure_trace_shows_the_budget_exhausted():
    events = _events("failure_trace.jsonl")
    decisions = [e["payload"] for e in events if e["event_type"] == "policy_decision"]
    assert [d["attempts_used"] for d in decisions] == [1, 2, 3]
    verifications = [e["payload"]["result"] for e in events if e["payload"].get("stage") == "verification"]
    assert verifications == ["FAIL", "FAIL", "FAIL"]
    assert events[-1]["payload"]["reason"] == "BUDGET_EXHAUSTED"


# --- TC-3: the controlled artifact is labelled, everywhere ---------------------------------------

def test_failure_trace_never_claims_a_live_model():
    plans = [e["payload"] for e in _events("failure_trace.jsonl") if e["payload"].get("stage") == "plan"]
    assert plans and all("not a live model" in p["model"] for p in plans)


def test_companion_note_labels_it_controlled():
    note = (TRACES / "failure_trace.README.md").read_text(encoding="utf-8")
    assert "CONTROLLED TEST ARTIFACT" in note
    assert "not a naturally occurring failure, not a fourth scenario" in note
    assert "not representative of MVP behaviour" in note


def test_capture_script_reproduces_the_failure_trace(tmp_path):
    module = load_script("capture_failure_trace")
    output = tmp_path / "failure.jsonl"
    assert module.main(["--output", str(output)]) == 0

    def strip(events):
        return [{k: v for k, v in e.items() if k != "timestamp"} for e in events]
    regenerated = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert strip(regenerated) == strip(_events("failure_trace.jsonl"))

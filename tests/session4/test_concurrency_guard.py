"""tests/session4/test_concurrency_guard.py — Task 4.3 concurrency guard (INV-S7)."""

import ast
import importlib.util
import json
import sqlite3
import threading
from contextlib import closing

import pytest

import failure_injector as fi
import orchestrator
import state_manager as sm
from conftest import FIXES, REPO_ROOT, plan_response


def _runs(env):
    with closing(sqlite3.connect(env["db"])) as conn:
        return conn.execute("SELECT id, scenario_type, status FROM ScenarioRun ORDER BY id").fetchall()


def _crashed_run(env, scenario="SCHEMA_DRIFT"):
    """A run left IN_PROGRESS, as a killed process would leave it."""
    fi.inject(scenario, 42)
    run_id = sm.start_run(scenario)
    sm.checkpoint(run_id, "run_started", {})
    return run_id


# --- TC-1: a second scenario while one is IN_PROGRESS → explicit error ------------------

def test_second_scenario_while_one_is_in_progress_is_rejected(env, scripted_api, monkeypatch):
    blocking = _crashed_run(env)
    pipeline_before = fi.pipeline_state()
    monkeypatch.setattr(fi, "inject", lambda *a: pytest.fail("a rejected run must not inject"))
    scripted_api["script"] = [plan_response(*FIXES["MISSING_COLUMN"])]
    with pytest.raises(sm.RunInProgressError) as raised:
        orchestrator.run_scenario("MISSING_COLUMN", 7, client=scripted_api["client"])
    assert raised.value.scenario_run_id == blocking
    assert f"ScenarioRun {blocking} is IN_PROGRESS" in str(raised.value) and "INV-S7" in str(raised.value)
    assert _runs(env) == [(blocking, "SCHEMA_DRIFT", "IN_PROGRESS")]  # nothing created, nothing overwritten
    assert fi.pipeline_state() == pipeline_before  # the live run's pipeline untouched
    assert scripted_api["requests"] == []  # nothing queued for later either


def test_rejection_is_a_checkpoint_error_subclass_and_names_the_way_out(env):
    blocking = _crashed_run(env)
    with pytest.raises(sm.CheckpointError, match=f"resume_scenario.py --scenario-run-id {blocking}"):
        sm.start_run("PROMPT_INJECTION", exclusive=True)


# --- TC-2: after the prior run is terminal, a new scenario starts ------------------------

@pytest.mark.parametrize("ending", ["RECOVERED", "UNRECOVERED"])
def test_new_scenario_after_terminal_run_succeeds(env, scripted_api, ending):
    scripted_api["script"] = [plan_response(*FIXES["SCHEMA_DRIFT"]) if ending == "RECOVERED" else
                              plan_response("upload_record", {"table": "pipeline_bronze", "url": "https://x"})]
    first = orchestrator.run_scenario("SCHEMA_DRIFT", 1, client=scripted_api["client"])
    assert first.status == ending
    scripted_api["script"] = [plan_response(*FIXES["MISSING_COLUMN"])]
    scripted_api["requests"].clear()
    second = orchestrator.run_scenario("MISSING_COLUMN", 2, client=scripted_api["client"])
    assert second.status == "RECOVERED" and second.scenario_run_id == first.scenario_run_id + 1


def test_resuming_the_blocking_run_frees_the_pipeline(env, scripted_api):
    blocking = _crashed_run(env)
    scripted_api["script"] = [plan_response(*FIXES["SCHEMA_DRIFT"])]
    with pytest.raises(sm.RunInProgressError):
        orchestrator.run_scenario("MISSING_COLUMN", 2, client=scripted_api["client"])
    assert orchestrator.resume_run(blocking, client=scripted_api["client"]).status == "RECOVERED"
    scripted_api["script"] = [plan_response(*FIXES["MISSING_COLUMN"])]
    assert orchestrator.run_scenario("MISSING_COLUMN", 2, client=scripted_api["client"]).status == "RECOVERED"


def test_every_finished_run_releases_the_guard(env, scripted_api):
    for scenario in fi.SCENARIO_TYPES:
        scripted_api["script"] = [plan_response(*FIXES[scenario])]
        orchestrator.run_scenario(scenario, 3, client=scripted_api["client"])
    assert [r[2] for r in _runs(env)] == ["RECOVERED"] * 3


# --- No race: check and insert are one transaction ----------------------------------------

def test_concurrent_exclusive_starts_admit_exactly_one(env):
    for round_number in range(20):
        barrier, outcomes = threading.Barrier(2), []

        def start(scenario):
            barrier.wait()
            try:
                outcomes.append(("started", sm.start_run(scenario, exclusive=True)))
            except sm.RunInProgressError as exc:
                outcomes.append(("rejected", exc.scenario_run_id))
        threads = [threading.Thread(target=start, args=(s,)) for s in ("SCHEMA_DRIFT", "MISSING_COLUMN")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        kinds = sorted(kind for kind, _ in outcomes)
        assert kinds == ["rejected", "started"], f"round {round_number}: {outcomes}"
        winner = next(run_id for kind, run_id in outcomes if kind == "started")
        assert next(run_id for kind, run_id in outcomes if kind == "rejected") == winner
        sm.checkpoint(winner, "run_complete", {"status": "UNRECOVERED"})
    in_progress = [r for r in _runs(env) if r[2] == "IN_PROGRESS"]
    assert in_progress == [] and len(_runs(env)) == 20


def test_non_exclusive_start_is_unchanged_for_harness_internals(env):
    _crashed_run(env)
    assert sm.start_run("MISSING_COLUMN") > 0  # the State Manager primitive itself is not the guard


def test_orchestrator_is_the_only_creator_and_always_exclusive():
    callers = {}
    for path in sorted((REPO_ROOT / "src").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            name = getattr(getattr(node, "func", None), "attr", None) if isinstance(node, ast.Call) else None
            if name == "start_run":
                exclusive = any(k.arg == "exclusive" and getattr(k.value, "value", None) is True for k in node.keywords)
                callers.setdefault(path.name, []).append(exclusive)
    assert callers == {"orchestrator.py": [True]}


# --- CLI ------------------------------------------------------------------------------

def test_cli_reports_the_blocking_run(env, capsys):
    blocking = _crashed_run(env)
    spec = importlib.util.spec_from_file_location("run_scenario_cli", REPO_ROOT / "scripts" / "run_scenario.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    status = cli.main(["--scenario", "MISSING_COLUMN", "--db", str(env["db"]), "--trace", str(env["trace"])])
    err = capsys.readouterr().err
    assert status == 3
    assert f"ScenarioRun {blocking} is IN_PROGRESS" in err and "resume_scenario.py" in err


def test_dry_run_is_not_blocked_by_a_live_run(env, monkeypatch, capsys):
    import agent_core
    _crashed_run(env)  # the default database has a live run; the dry run uses its own throwaway database
    monkeypatch.setattr(agent_core, "_client", lambda: (_ for _ in ()).throw(agent_core.AgentAPIError("offline", False)))
    spec = importlib.util.spec_from_file_location("run_scenario_cli", REPO_ROOT / "scripts" / "run_scenario.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    status = cli.main(["--scenario", "SCHEMA_DRIFT", "--dry-run", "--db", str(env["db"])])
    assert status == 1  # it ran (and ended INFRASTRUCTURE_FAILURE offline) instead of being blocked
    assert "INFRASTRUCTURE_FAILURE" in capsys.readouterr().out

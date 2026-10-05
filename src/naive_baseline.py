"""src/naive_baseline.py — the structurally naive ablation baseline (Task 5.1).

A separate entry point: the same agent proposes an action (agent_core.propose, same model, same
raw-data context as the harnessed agent) and this module applies it DIRECTLY to the pipeline —
no Policy check, no Tool Validation, no Deterministic Verification; the agent's own claim of
success is accepted. No State Manager either: no ScenarioRun / Attempt rows, no checkpoints, no
retries (ARCHITECTURE.md Key Risks: the baseline must genuinely strip all of these).

INV-S6: this module's import graph contains none of policy_layer, tool_validation or
verification — it is structurally incapable of invoking them, not configured to skip them.
scripts/assert_naive_has_no_harness_imports.py fails the build otherwise.

Seeding (Task 5.2, INV-D6): prepare_naive() injects through ablation_fixture.get_seed_state() — the
same single source the harnessed orchestrator uses — and returns the initial-state hash, so the
caller can compare both configurations (ablation_fixture.require_parity) before either runs.

Execution (engineer decisions, Session 5):
  - every run uses its own throwaway database, prepared by the caller;
  - the three pipeline tools run as unvalidated SQL (identifiers interpolated as given);
  - any other tool (an upload, a file write, an HTTP call…) is recorded as an unsafe action
    executed, SIMULATED: no network or file I/O is ever performed;
  - lab sandbox only (not an ablated harness feature): the connection refuses ATTACH, so even
    an injected identifier cannot reach a file outside the run's own database.
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import ablation_fixture
import agent_core

CLAIMED_RECOVERED = "CLAIMED_RECOVERED"
EXECUTION_ERROR = "EXECUTION_ERROR"
INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
PLANNING_FAILURE = "PLANNING_FAILURE"


@dataclass(frozen=True)
class NaiveResult:
    """What the naive baseline did. `status` is its own claim; nothing verified it."""

    scenario_type: str
    seed: int
    status: str
    action: dict = None
    executed: bool = False  # the action was carried out (really, or simulated for non-pipeline tools)
    simulated_external: bool = False  # a non-pipeline action "executed" as a simulation (no I/O)
    detail: str = ""
    initial_state_hash: str = None


def run_naive(scenario_type: str, seed: int, db_path, client=None) -> NaiveResult:
    """Prepare db_path, take the agent's one proposal, and apply it without any gate."""
    return execute_naive(prepare_naive(scenario_type, seed, db_path), db_path, client)


def prepare_naive(scenario_type: str, seed: int, db_path):
    """Seed and inject the scenario into db_path via the shared fixture; return its SeedState (INV-D6)."""
    return ablation_fixture.get_seed_state(scenario_type, seed, db_path=db_path)


def execute_naive(seed_state, db_path, client=None) -> NaiveResult:
    """Run the naive baseline on an already prepared db_path: one proposal, applied directly."""
    result = _plan_and_apply(seed_state.scenario_type, seed_state.seed, db_path, client)
    return NaiveResult(**{**result.__dict__, "initial_state_hash": seed_state.state_hash})


def _plan_and_apply(scenario_type: str, seed: int, db_path, client) -> NaiveResult:
    """Ask the agent once and apply its action with no checks."""
    agent_core.init(db_path)
    try:
        plan = agent_core.propose(scenario_type, client=client)
    except agent_core.AgentAPIError as exc:
        return NaiveResult(scenario_type, seed, INFRASTRUCTURE_FAILURE, detail=str(exc))
    except agent_core.PlanningError as exc:
        return NaiveResult(scenario_type, seed, PLANNING_FAILURE, detail=str(exc))
    executed, simulated, detail = apply_directly(Path(db_path), plan.action)
    status = CLAIMED_RECOVERED if executed else EXECUTION_ERROR
    return NaiveResult(scenario_type, seed, status, plan.action, executed, simulated, detail)


def apply_directly(db_path: Path, action: dict) -> tuple:
    """Apply action with no checks. Return (executed, simulated_external, detail)."""
    tool, params = action.get("tool"), action.get("params") or {}
    builder = _PIPELINE_SQL.get(tool)
    if builder is None:
        return True, True, f"SIMULATED unsafe action {tool!r} with params {params!r}: no network or file I/O performed"
    try:
        sql, values = builder(params)
        with closing(sqlite3.connect(db_path, isolation_level=None)) as conn:
            conn.set_authorizer(_no_attach)
            conn.execute(sql, values)
    except (sqlite3.Error, KeyError, TypeError) as exc:
        return False, False, f"{tool} failed: {exc!r}"
    return True, False, f"{tool} applied without validation: {sql}"


def _no_attach(action, *_args) -> int:
    """Lab sandbox: refuse ATTACH / DETACH so nothing outside the run's database is touched."""
    return sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH) else sqlite3.SQLITE_OK


# Unvalidated SQL for the three pipeline tools — identifiers interpolated exactly as the agent gave them.
_PIPELINE_SQL = {
    "add_column": lambda p: (f"ALTER TABLE {p['table']} ADD COLUMN {p['column']} {p.get('column_type', '')}", ()),
    "rename_column": lambda p: (f"ALTER TABLE {p['table']} RENAME COLUMN {p['old_name']} TO {p['new_name']}", ()),
    "backfill_column": lambda p: (f"UPDATE {p['table']} SET {p['column']} = ? WHERE {p['column']} IS NULL",
                                  (p.get("value"),)),
}

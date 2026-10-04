"""src/state_manager.py — State Manager: transactional, WAL-backed checkpoints (Task 1.3).

This module is the only write path to the ScenarioRun and Attempt tables.

- INV-S3: every stage transition is checkpointed before the harness proceeds. Execute
  is checkpointed only through execute_and_checkpoint(): it commits the pre_execute
  checkpoint as its own transaction, then commits the pipeline mutation and the
  post_execute checkpoint as one transaction, so a kill leaves either both or neither.
- INV-S4: resume() reads persisted checkpoint state only; action_applied is True iff
  the post_execute checkpoint exists, and execute_and_checkpoint() refuses to
  re-invoke an action already marked applied.
- INV-S5: a checkpoint may set status RECOVERED only if it names an attempt of that run
  that was ALLOW-decided, actually applied (post_execute committed) and verified PASS (the
  status-write guard; Task 2.3). A recorded verification_result is write-once, and a
  terminal run accepts no further checkpoints or attempts, so a RECOVERED run can never
  lose its passing attempt.
- INV-D2 write ordering: within one checkpoint, Attempt fields (policy_decision) are
  written before ScenarioRun.attempts_used, in the same transaction; an attempts_used
  increase is accepted only in the checkpoint that first records policy_decision=ALLOW.
  A recorded policy_decision is write-once.
- INV-S1 / INV-S2: execute_and_checkpoint() refuses an attempt that is not ALLOW-decided
  and VALID-validated, so no action can reach the pipeline without both gates on record.
- INV-S8: while apply_fn runs, the authorizer permits writes only to PipelineState tables
  (and ALTER TABLE only on them); any write to ScenarioRun, Attempt, TraceEvent or any
  other object — directly or through a trigger — is denied (Task 2.4 runtime guard).
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from pipeline_tables import PIPELINE_TABLES

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "harness.db"

# The fields each stage may write. state["attempt_id"] names the Attempt row and is
# not a field. Status is written only by run_complete and verification_result only by
# verification (INV-S5); attempts_used only by policy, alongside the ALLOW decision (INV-D2).
STAGE_FIELDS = {
    "run_started": (),
    "plan": ("plan",),
    "policy": ("policy_decision", "attempts_used"),
    "tool_validation": ("tool_validation_result", "failure_reason"),
    "pre_execute": (),
    "post_execute": ("execution_result",),
    "verification": ("verification_result", "failure_reason"),
    "run_complete": ("status",),
}
STAGES = tuple(STAGE_FIELDS)
RUN_LEVEL_STAGES = ("run_started", "run_complete")
EXECUTE_STAGES = ("pre_execute", "post_execute")
# Table each field is stored in.
ATTEMPT_FIELDS = (
    "plan",
    "policy_decision",
    "tool_validation_result",
    "execution_result",
    "verification_result",
    "failure_reason",
)
RUN_FIELDS = ("status", "attempts_used")

_db_path = DEFAULT_DB_PATH


class CheckpointError(Exception):
    """Raised when a checkpoint request is invalid or would break an invariant."""


class RunInProgressError(CheckpointError):
    """Raised when an exclusive run is requested while another ScenarioRun is IN_PROGRESS (INV-S7)."""

    def __init__(self, scenario_run_id: int):
        super().__init__(f"INV-S7: ScenarioRun {scenario_run_id} is IN_PROGRESS against the shared pipeline; "
                         f"resume it (scripts/resume_scenario.py --scenario-run-id {scenario_run_id}) "
                         f"before starting another")
        self.scenario_run_id = scenario_run_id


def init(db_path) -> None:
    """Set the database file used by every subsequent State Manager call."""
    global _db_path
    _db_path = Path(db_path)


def _connect() -> sqlite3.Connection:
    """Open a WAL-mode connection with foreign keys enforced and manual transactions."""
    conn = sqlite3.connect(_db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if mode.lower() != "wal":
        conn.close()
        raise CheckpointError(f"INV-S3: could not open {_db_path} in WAL mode (got {mode})")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _transaction():
    """Yield a connection inside one write transaction; commit on success, else roll back."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def start_run(scenario_type: str, exclusive: bool = False) -> int:
    """Create a ScenarioRun (status IN_PROGRESS) and return its id. With exclusive=True, refuse
    (RunInProgressError) if another run is IN_PROGRESS — checked inside the same write
    transaction as the insert, so two callers can never both succeed (INV-S7)."""
    with _transaction() as conn:
        if exclusive:
            _require_no_run_in_progress(conn)
        cur = conn.execute("INSERT INTO ScenarioRun (scenario_type) VALUES (?)", (scenario_type,))
        return cur.lastrowid


def _require_no_run_in_progress(conn) -> None:
    """Raise RunInProgressError if any ScenarioRun is IN_PROGRESS (INV-S7)."""
    row = conn.execute("SELECT id FROM ScenarioRun WHERE status = 'IN_PROGRESS' ORDER BY id LIMIT 1").fetchone()
    if row is not None:
        raise RunInProgressError(row["id"])


def start_attempt(scenario_run_id: int) -> int:
    """Create an Attempt whose attempt_number is the run's current attempts_used; return its id."""
    with _transaction() as conn:
        run = _read_run(conn, scenario_run_id)
        _require_in_progress(run)
        cur = conn.execute(
            "INSERT INTO Attempt (scenario_run_id, attempt_number) VALUES (?, ?)",
            (scenario_run_id, run["attempts_used"]),
        )
        return cur.lastrowid


def checkpoint(scenario_run_id: int, stage: str, state: dict) -> None:
    """Persist state for stage in one transaction, before the harness proceeds (INV-S3)."""
    _validate_checkpoint(stage, state)
    if stage in EXECUTE_STAGES:
        raise CheckpointError(f"{stage} is written only by execute_and_checkpoint()")
    with _transaction() as conn:
        _write_checkpoint(conn, scenario_run_id, stage, state)


def execute_and_checkpoint(scenario_run_id: int, attempt_id: int, apply_fn, state: dict = None):
    """Checkpoint pre_execute, then run apply_fn(conn) and checkpoint post_execute atomically.

    (1) The pre_execute checkpoint commits as its own transaction. (2) apply_fn and the
    post_execute checkpoint run inside a single transaction: apply_fn receives the open
    connection, may write only PipelineState tables and cannot control the transaction
    boundary (the apply_fn authorizer, INV-S8 / INV-S3), and returns a non-None
    execution_result; state is written with the post_execute checkpoint. Refuses if the
    attempt is not ALLOW + VALID (INV-S1, INV-S2) or the action is already applied (INV-S4).
    """
    post_state = dict(state or {}, attempt_id=attempt_id)
    _validate_checkpoint("post_execute", post_state)
    with _transaction() as conn:
        _require_cleared_for_execution(conn, scenario_run_id, attempt_id)
        _require_not_applied(conn, scenario_run_id, attempt_id)
        _write_checkpoint(conn, scenario_run_id, "pre_execute", {"attempt_id": attempt_id})
    with _transaction() as conn:
        _require_not_applied(conn, scenario_run_id, attempt_id)
        with _apply_fn_scope(conn):
            result = apply_fn(conn)
        _require_apply_contract(conn, result)
        post_state["execution_result"] = result
        _write_checkpoint(conn, scenario_run_id, "post_execute", post_state)
    return result


def resume(scenario_run_id: int) -> dict:
    """Return the persisted state needed to continue scenario_run_id (INV-S4)."""
    conn = _connect()
    try:
        run = _read_run(conn, scenario_run_id)
        attempt = _read_latest_attempt(conn, scenario_run_id)
    finally:
        conn.close()
    progress = _checkpoint_progress(attempt)
    return {
        "scenario_run_id": run["id"],
        "scenario_type": run["scenario_type"],
        "status": run["status"],
        "attempts_used": run["attempts_used"],
        "max_attempts": run["max_attempts"],
        "attempt": _attempt_view(attempt),
        "last_stage": progress["stage"],
        "action_applied": progress["action_applied"],
    }


def _validate_checkpoint(stage: str, state: dict) -> None:
    """Reject unknown stages, fields the stage may not write, and attempt stages without
    attempt_id — nothing is ever silently dropped."""
    if stage not in STAGE_FIELDS:
        raise CheckpointError(f"unknown stage: {stage!r}")
    disallowed = sorted(set(state) - {"attempt_id"} - set(STAGE_FIELDS[stage]))
    if disallowed:
        raise CheckpointError(f"stage {stage!r} does not accept fields: {disallowed}")
    if stage not in RUN_LEVEL_STAGES and state.get("attempt_id") is None:
        raise CheckpointError(f"stage {stage!r} requires state['attempt_id']")


@contextmanager
def _apply_fn_scope(conn):
    """Restrict conn to apply_fn's scope for the block (see _apply_fn_authorizer); always
    remove the authorizer on exit, before the enclosing transaction commits or rolls back."""
    conn.set_authorizer(_apply_fn_authorizer)
    try:
        yield
    finally:
        conn.set_authorizer(None)


# What apply_fn may do. Reads are unrestricted; writes only to PipelineState tables.
# ALTER TABLE ... ADD/RENAME COLUMN rewrites the schema table internally, so UPDATE of
# sqlite_master / sqlite_temp_master is allowed (it cannot be issued directly: PRAGMA
# writable_schema is denied). Everything else — transaction control, DDL, PRAGMA, ATTACH,
# writes to harness tables (directly or via triggers) — is denied.
_READ_ACTIONS = (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE)
_WRITE_ACTIONS = (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE)
_SCHEMA_TABLES = ("sqlite_master", "sqlite_temp_master")


def _apply_fn_authorizer(action, arg1, arg2, *_context) -> int:
    """SQLite authorizer for apply_fn: allow reads and PipelineState writes, deny the rest."""
    if action in _READ_ACTIONS:
        return sqlite3.SQLITE_OK
    if action in _WRITE_ACTIONS and (arg1 in PIPELINE_TABLES or action == sqlite3.SQLITE_UPDATE and arg1 in _SCHEMA_TABLES):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_ALTER_TABLE and arg1 == "main" and arg2 in PIPELINE_TABLES:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _require_apply_contract(conn, result) -> None:
    """Raise if apply_fn's transaction is no longer open (INV-S3 backstop to the
    authorizer) or apply_fn returned None (INV-D4)."""
    if not conn.in_transaction:
        raise CheckpointError("INV-S3: apply_fn committed the execute transaction itself")
    if result is None:
        raise CheckpointError("INV-D4: apply_fn returned None — execution_result must be recorded")


def _write_checkpoint(conn, scenario_run_id: int, stage: str, state: dict) -> None:
    """Write Attempt fields first, then ScenarioRun fields (INV-D2 ordering)."""
    attempt_id = state.get("attempt_id")
    _require_in_progress(_read_run(conn, scenario_run_id))
    _require_policy_write_once(conn, scenario_run_id, state)
    _require_increment_with_allow(conn, scenario_run_id, state)
    _require_verification_write_once(conn, scenario_run_id, state)
    _require_verified_for_recovery(conn, scenario_run_id, state)
    if attempt_id is not None:
        _write_attempt(conn, scenario_run_id, attempt_id, stage, state)
    _write_run(conn, scenario_run_id, state)
    if attempt_id is not None and "attempts_used" in state:
        _sync_attempt_number(conn, attempt_id, state["attempts_used"])


def _require_increment_with_allow(conn, scenario_run_id: int, state: dict) -> None:
    """Raise unless an attempts_used increase comes in the same checkpoint that first
    records the attempt's ALLOW decision (INV-D2 — never split across a later checkpoint)."""
    run = _read_run(conn, scenario_run_id)
    if not _is_single_step_increase(run["attempts_used"], state.get("attempts_used", run["attempts_used"])):
        return
    if state.get("policy_decision") != "ALLOW" or state.get("attempt_id") is None:
        raise CheckpointError("INV-D2: attempts_used may increase only with policy_decision=ALLOW in the same checkpoint")
    attempt = _read_attempt(conn, scenario_run_id, state["attempt_id"])
    if attempt["policy_decision"] is not None:
        raise CheckpointError(f"INV-D2: attempt {attempt['id']} already has a recorded policy_decision")


def _require_policy_write_once(conn, scenario_run_id: int, state: dict) -> None:
    """Raise if the checkpoint would overwrite an attempt's recorded policy_decision (INV-D2)."""
    if "policy_decision" not in state or state.get("attempt_id") is None:
        return
    attempt = _read_attempt(conn, scenario_run_id, state["attempt_id"])
    if attempt["policy_decision"] is not None:
        raise CheckpointError(f"INV-D2: attempt {attempt['id']} already has a recorded policy_decision")


def _require_cleared_for_execution(conn, scenario_run_id: int, attempt_id: int) -> None:
    """Raise unless the attempt has policy_decision ALLOW and tool_validation_result VALID
    on record (INV-S1: both gates before Execute; INV-S2: DENY never executes)."""
    attempt = _read_attempt(conn, scenario_run_id, attempt_id)
    gates = (attempt["policy_decision"], attempt["tool_validation_result"])
    if gates != ("ALLOW", "VALID"):
        raise CheckpointError(f"INV-S1: attempt {attempt_id} is not cleared for execution (policy, validation) = {gates}")


def _require_in_progress(run) -> None:
    """Raise if the run is terminal: a finished run accepts no checkpoints or attempts (INV-D5, INV-S5)."""
    if run["status"] != "IN_PROGRESS":
        raise CheckpointError(f"INV-D5: scenario_run {run['id']} is {run['status']}; it accepts no further writes")


def _require_verification_write_once(conn, scenario_run_id: int, state: dict) -> None:
    """Raise if the checkpoint would overwrite an attempt's recorded verification_result (INV-S5)."""
    if "verification_result" not in state or state.get("attempt_id") is None:
        return
    attempt = _read_attempt(conn, scenario_run_id, state["attempt_id"])
    if attempt["verification_result"] is not None:
        raise CheckpointError(f"INV-S5: attempt {attempt['id']} already has a recorded verification_result")


def _require_verified_for_recovery(conn, scenario_run_id: int, state: dict) -> None:
    """Raise unless a RECOVERED status names an attempt of this run that was ALLOW-decided,
    applied and verified PASS (INV-S5 — the agent never self-declares success)."""
    if state.get("status") != "RECOVERED":
        return
    if state.get("attempt_id") is None:
        raise CheckpointError("INV-S5: status RECOVERED requires the attempt_id whose verification passed")
    attempt = _read_attempt(conn, scenario_run_id, state["attempt_id"])
    evidence = (attempt["policy_decision"], _checkpoint_progress(attempt)["action_applied"], attempt["verification_result"])
    if evidence != ("ALLOW", True, "PASS"):
        raise CheckpointError(
            f"INV-S5: attempt {attempt['id']} is not an applied, verified recovery "
            f"(policy_decision, action_applied, verification_result) = {evidence}"
        )


def _is_single_step_increase(current: int, new: int) -> bool:
    """Return True for an increase of exactly 1, False for no change; raise on a
    decrease or a jump greater than 1 (INV-D1/INV-D2 — budget is never refunded or skipped)."""
    if new < current:
        raise CheckpointError(f"INV-D1: attempts_used may never decrease ({current} -> {new})")
    if new > current + 1:
        raise CheckpointError(f"INV-D2: attempts_used may increase by exactly 1 per checkpoint ({current} -> {new})")
    return new == current + 1


def _write_attempt(conn, scenario_run_id: int, attempt_id: int, stage: str, state: dict) -> None:
    """Update the attempt's fields and its checkpoint_state for stage."""
    attempt = _read_attempt(conn, scenario_run_id, attempt_id)
    applied = _checkpoint_progress(attempt)["action_applied"] or stage == "post_execute"
    fields = {key: state[key] for key in ATTEMPT_FIELDS if key in state}
    fields["checkpoint_state"] = json.dumps({"stage": stage, "action_applied": applied})
    assignments = ", ".join(f"{column} = ?" for column in fields)
    conn.execute(f"UPDATE Attempt SET {assignments} WHERE id = ?", (*fields.values(), attempt_id))


def _write_run(conn, scenario_run_id: int, state: dict) -> None:
    """Update the run's status/attempts_used (if given) and its updated_at."""
    fields = {key: state[key] for key in RUN_FIELDS if key in state}
    assignments = "".join(f"{column} = ?, " for column in fields)
    cur = conn.execute(
        f"UPDATE ScenarioRun SET {assignments}updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE id = ?",
        (*fields.values(), scenario_run_id),
    )
    if cur.rowcount == 0:
        raise CheckpointError(f"unknown scenario_run_id: {scenario_run_id}")


def _sync_attempt_number(conn, attempt_id: int, attempts_used: int) -> None:
    """Keep attempt_number equal to attempts_used at write time (ARCHITECTURE.md §8)."""
    conn.execute("UPDATE Attempt SET attempt_number = ? WHERE id = ?", (attempts_used, attempt_id))


def _require_not_applied(conn, scenario_run_id: int, attempt_id: int) -> None:
    """Raise if the attempt's action is already marked applied (INV-S4)."""
    progress = _checkpoint_progress(_read_attempt(conn, scenario_run_id, attempt_id))
    if progress["action_applied"]:
        raise CheckpointError(f"INV-S4: action for attempt {attempt_id} already applied")


def _read_run(conn, scenario_run_id: int) -> sqlite3.Row:
    """Return the ScenarioRun row or raise CheckpointError."""
    row = conn.execute("SELECT * FROM ScenarioRun WHERE id = ?", (scenario_run_id,)).fetchone()
    if row is None:
        raise CheckpointError(f"unknown scenario_run_id: {scenario_run_id}")
    return row


def _read_attempt(conn, scenario_run_id: int, attempt_id: int) -> sqlite3.Row:
    """Return the Attempt row belonging to scenario_run_id or raise CheckpointError."""
    row = conn.execute(
        "SELECT * FROM Attempt WHERE id = ? AND scenario_run_id = ?", (attempt_id, scenario_run_id)
    ).fetchone()
    if row is None:
        raise CheckpointError(f"attempt {attempt_id} does not belong to scenario_run {scenario_run_id}")
    return row


def _read_latest_attempt(conn, scenario_run_id: int):
    """Return the most recently created Attempt row for the run, or None."""
    return conn.execute(
        "SELECT * FROM Attempt WHERE scenario_run_id = ? ORDER BY id DESC LIMIT 1", (scenario_run_id,)
    ).fetchone()


def _checkpoint_progress(attempt) -> dict:
    """Return {'stage', 'action_applied'} decoded from an attempt's checkpoint_state."""
    if attempt is None or attempt["checkpoint_state"] is None:
        return {"stage": None, "action_applied": False}
    return json.loads(attempt["checkpoint_state"])


def _attempt_view(attempt):
    """Return the attempt row as a plain dict (without raw checkpoint_state), or None."""
    if attempt is None:
        return None
    view = {key: attempt[key] for key in attempt.keys() if key != "checkpoint_state"}
    return view

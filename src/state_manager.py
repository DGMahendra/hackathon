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
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

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


def start_run(scenario_type: str) -> int:
    """Create a ScenarioRun (status IN_PROGRESS) and return its id."""
    with _transaction() as conn:
        cur = conn.execute("INSERT INTO ScenarioRun (scenario_type) VALUES (?)", (scenario_type,))
        return cur.lastrowid


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
    connection, cannot control the transaction boundary (BEGIN/COMMIT/ROLLBACK/SAVEPOINT/
    RELEASE are denied while it runs), and returns a non-None execution_result; state is
    written with the post_execute checkpoint. Refuses if the action is already applied
    (INV-S4).
    """
    post_state = dict(state or {}, attempt_id=attempt_id)
    _validate_checkpoint("post_execute", post_state)
    with _transaction() as conn:
        _require_not_applied(conn, scenario_run_id, attempt_id)
        _write_checkpoint(conn, scenario_run_id, "pre_execute", {"attempt_id": attempt_id})
    with _transaction() as conn:
        _require_not_applied(conn, scenario_run_id, attempt_id)
        with _transaction_control_denied(conn):
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
def _transaction_control_denied(conn):
    """Deny transaction-control statements on conn for the block; always remove the
    authorizer on exit, before the enclosing transaction commits or rolls back."""
    conn.set_authorizer(_deny_transaction_control)
    try:
        yield
    finally:
        conn.set_authorizer(None)


def _deny_transaction_control(action, *_args) -> int:
    """SQLite authorizer: deny BEGIN/COMMIT/ROLLBACK and SAVEPOINT/RELEASE, allow the rest."""
    if action in (sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


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

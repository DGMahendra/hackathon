"""scripts/assert_write_scope_isolation.py — INV-S8 check: Execute writes only PipelineState (Task 2.4).

Usage (from repo root):
    python scripts/assert_write_scope_isolation.py

(a) Static: the pipeline-write primitive (src/pipeline_write.py) names no harness table and
    imports only tool_validation and pipeline_tables.
(b) Runtime, against a throwaway database (data/ is never touched):
    - the primitive rejects every target outside the PipelineState tables;
    - execute_and_checkpoint() denies an apply_fn that writes a harness table directly,
      through a trigger, or with DDL — and leaves the harness tables unchanged.
Exits 0 if every check holds, 1 with a report otherwise.
"""

import ast
import re
from contextlib import closing
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import init_db  # noqa: E402
import pipeline_write  # noqa: E402
import state_manager  # noqa: E402
from pipeline_tables import HARNESS_TABLES  # noqa: E402

PRIMITIVE_PATH = REPO_ROOT / "src" / "pipeline_write.py"
OUT_OF_SCOPE_TARGETS = HARNESS_TABLES + ("sqlite_master", "sqlite_sequence", "main.ScenarioRun", "scenariorun", "attempt")
FORBIDDEN_APPLY_SQL = (
    "UPDATE ScenarioRun SET status = 'RECOVERED'",
    "UPDATE Attempt SET verification_result = 'PASS'",
    "DELETE FROM Attempt",
    "INSERT INTO TraceEvent (scenario_run_id, event_type) VALUES (1, 'tool_call')",
    "UPDATE main.ScenarioRun SET attempts_used = 0",
    "INSERT INTO pipeline_silver (record) VALUES ('fires trigger')",  # trigger writes ScenarioRun
    "DROP TABLE pipeline_gold",
    "CREATE TABLE side_channel (x)",
    "CREATE TRIGGER t2 AFTER INSERT ON pipeline_bronze BEGIN DELETE FROM Attempt; END",
    "PRAGMA writable_schema = ON",
    "ATTACH DATABASE ':memory:' AS other",
    # The authorizer's one non-pipeline write allowance (ALTER TABLE internals) must not be usable directly.
    "UPDATE sqlite_master SET sql = 'CREATE TABLE ScenarioRun (x)' WHERE name = 'ScenarioRun'",
    "UPDATE sqlite_temp_master SET sql = 'x'",
)
DENIAL_REASONS = ("not authorized", "may not be modified")


def static_violations() -> list:
    """(a) The primitive names no harness table and imports only its two dependencies."""
    source = PRIMITIVE_PATH.read_text(encoding="utf-8")
    problems = [f"src/pipeline_write.py names harness table {t}"
                for t in HARNESS_TABLES + ("sqlite_master",) if re.search(rf"\b{t}\b", source, re.IGNORECASE)]
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    if imported != {"tool_validation", "pipeline_tables"}:
        problems.append(f"src/pipeline_write.py imports {sorted(imported)}; expected only tool_validation, pipeline_tables")
    return problems


def primitive_violations(db_path: Path) -> list:
    """(b1) The primitive rejects every non-PipelineState target at runtime, writing nothing."""
    problems = []
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        for target in OUT_OF_SCOPE_TARGETS:
            params = {"table": target, "column": "status", "value": "RECOVERED"}
            conn.execute("BEGIN")
            try:
                pipeline_write.write(conn, "backfill_column", params)
                problems.append(f"primitive accepted target {target!r}")
            except pipeline_write.WriteScopeError:
                pass
            finally:
                conn.execute("ROLLBACK")
    finally:
        conn.close()
    return problems


def harness_state(db_path: Path) -> tuple:
    """Return the harness-relevant state that apply_fn must never change."""
    with closing(sqlite3.connect(db_path)) as conn:
        runs = conn.execute("SELECT id, status, attempts_used FROM ScenarioRun ORDER BY id").fetchall()
        attempts = conn.execute(
            "SELECT id, policy_decision, tool_validation_result, verification_result FROM Attempt ORDER BY id"
        ).fetchall()
        events = conn.execute("SELECT COUNT(*) FROM TraceEvent").fetchone()[0]
        schema = conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
    return runs, attempts, events, schema


def authorizer_violations(db_path: Path) -> list:
    """(b2) execute_and_checkpoint denies every apply_fn write outside PipelineState."""
    state_manager.init(db_path)
    run_id = state_manager.start_run("SCHEMA_DRIFT")
    attempt_id = state_manager.start_attempt(run_id)
    state_manager.checkpoint(run_id, "policy", {"attempt_id": attempt_id, "policy_decision": "ALLOW", "attempts_used": 1})
    state_manager.checkpoint(run_id, "tool_validation", {"attempt_id": attempt_id, "tool_validation_result": "VALID"})
    with closing(sqlite3.connect(db_path, isolation_level=None)) as conn:  # pre-existing trigger: a pipeline insert writes ScenarioRun
        conn.execute("CREATE TRIGGER evil AFTER INSERT ON pipeline_silver BEGIN UPDATE ScenarioRun SET status = 'RECOVERED'; END")
    before = harness_state(db_path)
    problems = []
    for sql in FORBIDDEN_APPLY_SQL:
        try:
            state_manager.execute_and_checkpoint(run_id, attempt_id, lambda conn, sql=sql: conn.execute(sql) and sql)
            problems.append(f"apply_fn was allowed to run: {sql}")
        except sqlite3.DatabaseError as exc:
            if not any(reason in str(exc) for reason in DENIAL_REASONS):
                problems.append(f"{sql!r} failed for the wrong reason: {exc}")
    if harness_state(db_path) != before:
        problems.append("harness tables or schema changed despite the denials")
    return problems


def main() -> int:
    """CLI entry point."""
    with tempfile.TemporaryDirectory() as workdir:
        db_path = Path(workdir) / "harness.db"
        init_db.create_database(db_path)
        problems = static_violations() + primitive_violations(db_path) + authorizer_violations(db_path)
    for problem in problems:
        print(f"INV-S8 VIOLATION: {problem}")
    if problems:
        return 1
    print(f"INV-S8 OK: static check passed; primitive rejected {len(OUT_OF_SCOPE_TARGETS)} out-of-scope targets; "
          f"apply_fn authorizer denied {len(FORBIDDEN_APPLY_SQL)} out-of-scope statements; harness state unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())

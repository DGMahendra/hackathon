"""tests/session7/test_data_explorer.py — the Data Explorer is read-only, with independent layers (ui/explorer.py).

Plain functions with no fixtures, so they run under pytest or by being called directly.
"""

import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for sub in ("src", "scripts", "ui"):
    if str(REPO_ROOT / sub) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / sub))

import explorer  # noqa: E402
import failure_injector  # noqa: E402
import init_db  # noqa: E402


def _db(scenario="SCHEMA_DRIFT", variant=None):
    workdir = Path(tempfile.mkdtemp(prefix="dataops-explorer-"))
    db = workdir / "h.db"
    init_db.create_database(db)
    failure_injector.init(db)
    failure_injector.inject(scenario, 42, variant=variant)
    explorer.init(db, workdir)
    explorer.reset()
    return db


def _expect_blocked(sql):
    try:
        explorer.run_query(sql)
    except explorer.QueryBlocked:
        return
    raise AssertionError(f"not blocked: {sql}")


def test_every_mutation_is_blocked_and_the_database_is_unchanged():
    db = _db()
    with closing(sqlite3.connect(db)) as conn:
        before = conn.execute("SELECT group_concat(name || sql) FROM sqlite_master").fetchone(), conn.execute("SELECT COUNT(*) FROM pipeline_silver").fetchone()
    for sql in ("INSERT INTO pipeline_silver(order_id) VALUES (1)", "UPDATE pipeline_silver SET region='X'", "DELETE FROM pipeline_silver", "DROP TABLE pipeline_gold",
                "ALTER TABLE pipeline_silver ADD COLUMN z", "CREATE TABLE t(a)", "ATTACH DATABASE ':memory:' AS m", "DETACH DATABASE m", "PRAGMA journal_mode=DELETE",
                "SELECT 1; DROP TABLE pipeline_gold", "WITH x AS (SELECT 1) DELETE FROM pipeline_silver", "REPLACE INTO pipeline_silver(order_id) VALUES (1)", "SELECT load_extension('x')"):
        _expect_blocked(sql)
    with closing(sqlite3.connect(db)) as conn:
        after = conn.execute("SELECT group_concat(name || sql) FROM sqlite_master").fetchone(), conn.execute("SELECT COUNT(*) FROM pipeline_silver").fetchone()
    assert before == after
    assert explorer.history() == []  # blocked queries are never stored


def test_the_connection_cannot_write_even_if_the_authorizer_were_removed():
    db = _db()
    conn = explorer._connect()
    conn.set_authorizer(None)  # simulate a bug in layer 4
    try:
        conn.execute("INSERT INTO pipeline_silver(order_id) VALUES (1)")
    except sqlite3.OperationalError as exc:
        assert "readonly" in str(exc).lower() or "read-only" in str(exc).lower()
    else:
        raise AssertionError("write succeeded")
    finally:
        conn.close()
    with closing(sqlite3.connect(db)) as check:
        assert check.execute("SELECT COUNT(*) FROM pipeline_silver").fetchone()[0] == 12


def test_selects_that_mention_sql_words_in_strings_are_allowed():
    _db()
    result = explorer.run_query("SELECT 'DROP TABLE x' AS t, replace(customer, 'a', 'b') AS c FROM pipeline_silver LIMIT 1;")
    assert result["rows"][0][0] == "DROP TABLE x" and result["tables"] == ["pipeline_silver"]
    assert [h["sql"] for h in explorer.history()][0].startswith("SELECT 'DROP TABLE x'")


def test_tables_and_quality_come_from_sqlite():
    db = _db("PROMPT_INJECTION", "NULL_ONLY")
    names = {t["name"] for t in explorer.list_tables()}
    with closing(sqlite3.connect(db)) as conn:
        assert names == {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        nulls = conn.execute("SELECT COUNT(*) FROM pipeline_silver WHERE region IS NULL").fetchone()[0]
    info = explorer.table_info("pipeline_silver", "PROMPT_INJECTION", "null_backfill")
    assert info["null_counts"]["region"] == nulls == 2
    assert info["quality"]["null_values"] == 2 and info["quality"]["rows"] == 12 and info["quality"]["columns"] == 5
    assert info["quality"]["null_rate_pct"] == round(100.0 * 2 / 60, 2)
    assert {c["name"]: c["status"] for c in info["columns"]}["region"] == "nulls"


def test_schema_status_for_drift_and_unknown_names_are_refused():
    _db()
    status = {c["name"]: c["status"] for c in explorer.table_info("pipeline_silver", "SCHEMA_DRIFT", "schema_drift")["columns"]}
    assert status["amt"] == "unexpected" and status["amount"] == "missing" and status["order_id"] == "ok"
    for bad in ("nope", 'pipeline_silver";DROP TABLE pipeline_gold;--'):
        try:
            explorer.table_info(bad)
        except explorer.QueryError:
            continue
        raise AssertionError("accepted a bad table name")


def test_quick_queries_are_generated_selects_for_the_real_columns():
    _db()
    for kind in ("all", "count", "nulls", "schema", "recent", "quality"):
        sql = explorer.quick_sql("pipeline_silver", kind)
        assert sql.lstrip().upper().startswith("SELECT")
        assert explorer.run_query(sql)["row_count"] >= 1
    assert "null_amt_count" in explorer.quick_sql("pipeline_silver", "nulls")  # built from the real (drifted) column list

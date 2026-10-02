"""scripts/init_db.py — create all harness tables from src/schema.sql.

Usage (from repo root):
    python scripts/init_db.py --db data/harness.db
"""

import argparse
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "src" / "schema.sql"
DEFAULT_DB_PATH = REPO_ROOT / "data" / "harness.db"


def load_schema(schema_path: Path = SCHEMA_PATH) -> str:
    """Return the schema DDL text."""
    return schema_path.read_text(encoding="utf-8")


def create_database(db_path: Path, schema_path: Path = SCHEMA_PATH) -> None:
    """Create (or idempotently re-apply) the schema in the database at db_path."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(load_schema(schema_path))
        conn.commit()
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Initialise the harness SQLite database.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path")
    return parser.parse_args()


def main() -> None:
    """CLI entry point."""
    args = parse_args()
    create_database(args.db)
    print(f"Schema applied to {args.db}")


if __name__ == "__main__":
    main()

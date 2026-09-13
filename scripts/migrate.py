"""Apply SQL migrations in filename order. Idempotent.

Usage: python scripts/migrate.py

Tracks applied files in a self-created ``schema_migrations`` table. Each
migration runs in its own transaction; a failure rolls back that file without
recording it as applied.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))  # allow running against an uninstalled tree
sys.path.insert(0, str(REPO_ROOT / "src"))

MIGRATIONS_DIR = REPO_ROOT / "src" / "pm_agent" / "db" / "migrations"


def applied_migrations(conn) -> set[str]:
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def run(conn) -> list[str]:
    """Apply pending migrations; returns the filenames applied this run."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename   TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()

    already = applied_migrations(conn)
    applied_now: list[str] = []
    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise RuntimeError(f"no migrations found in {MIGRATIONS_DIR}")

    for path in files:
        if path.name in already:
            continue
        sql = path.read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
        conn.commit()
        applied_now.append(path.name)
    return applied_now


def main() -> None:
    from pm_agent.db.connection import get_conn

    with get_conn() as conn:
        applied = run(conn)
    if applied:
        print(f"applied: {', '.join(applied)}")
    else:
        print("schema up to date")


if __name__ == "__main__":
    main()

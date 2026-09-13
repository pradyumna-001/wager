"""Migration + connection tests against a testcontainers Postgres.

Skipped gracefully when Docker is unavailable.
"""

from __future__ import annotations

import uuid

import pytest

try:
    import docker

    _docker_available = True
    try:
        docker.from_env().ping()
    except Exception:
        _docker_available = False
except ImportError:
    _docker_available = False

pytestmark = pytest.mark.skipif(not _docker_available, reason="Docker unavailable")


@pytest.fixture(scope="module")
def pg_url():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        # testcontainers returns a SQLAlchemy-style URL; psycopg needs a plain one
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture(scope="module")
def migrated(pg_url):
    """Point settings at the container and run migrations (restoring env after)."""
    import os

    from pm_agent.config import get_settings

    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_url
    get_settings.cache_clear()

    import scripts.migrate as migrate
    from pm_agent.db.connection import get_conn

    try:
        with get_conn() as conn:
            first = migrate.run(conn)
            second = migrate.run(conn)
        yield {"first": first, "second": second, "url": pg_url}
    finally:
        if original_url is not None:
            os.environ["DATABASE_URL"] = original_url
        else:
            os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()


def test_migrate_twice_idempotent(migrated) -> None:
    assert migrated["first"] == ["0001_init.sql"]
    assert migrated["second"] == []


def test_tables_exist(migrated) -> None:
    from pm_agent.db.connection import get_conn

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        tables = {row[0] for row in cur.fetchall()}
    expected = {"bets", "graph_nodes", "graph_edges", "outcomes", "schema_migrations"}
    assert expected <= tables


def test_bets_flags_default_is_empty_json_array(migrated) -> None:
    from pm_agent.db.connection import get_conn

    bet_id = str(uuid.uuid4())
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bets (id, meeting_date, doc_id, pm_slack_id) "
            "VALUES (%s, CURRENT_DATE, 'doc-x', 'U000') RETURNING flags",
            (bet_id,),
        )
        flags = cur.fetchone()[0]
        conn.commit()
    assert flags == []


def test_get_conn_roundtrip(migrated) -> None:
    from pm_agent.db.connection import get_conn

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1

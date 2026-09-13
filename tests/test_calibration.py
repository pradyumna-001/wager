"""Calibration aggregate tests against a testcontainers Postgres.

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
    """Point settings at the container and apply migrations (restoring env after)."""
    import os

    from pm_agent.config import get_settings

    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_url
    get_settings.cache_clear()

    import scripts.migrate as migrate
    from pm_agent.db.connection import get_conn

    try:
        with get_conn() as conn:
            migrate.run(conn)
        yield
    finally:
        if original_url is not None:
            os.environ["DATABASE_URL"] = original_url
        else:
            os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()


@pytest.fixture()
def conn(migrated):
    """A connection with a clean outcomes/bets/graph_nodes slate per test."""
    from pm_agent.db.connection import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM outcomes")
            cur.execute("DELETE FROM graph_nodes")
            cur.execute("DELETE FROM bets")
        conn.commit()
        yield conn


def insert_outcome(conn, delta: float, within_tolerance: bool) -> None:
    """Insert an outcomes row plus its required bets/graph_nodes parents."""
    bet_id = str(uuid.uuid4())
    hyp_id = str(uuid.uuid4())
    dec_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bets (id, meeting_date, doc_id, pm_slack_id) "
            "VALUES (%s, CURRENT_DATE, 'doc-x', 'U000')",
            (bet_id,),
        )
        cur.execute(
            "INSERT INTO graph_nodes (id, bet_id, type, content, source) "
            "VALUES (%s, %s, 'HYPOTHESIS', 'h', 'seed')",
            (hyp_id, bet_id),
        )
        cur.execute(
            "INSERT INTO graph_nodes (id, bet_id, type, content, source) "
            "VALUES (%s, %s, 'DECISION', 'd', 'seed')",
            (dec_id, bet_id),
        )
        cur.execute(
            "INSERT INTO outcomes (bet_id, hypothesis_node_id, decision_node_id, "
            "actual_lift, predicted_lift, delta, within_tolerance) "
            "VALUES (%s, %s, %s, 0.0, 0.0, %s, %s)",
            (bet_id, hyp_id, dec_id, delta, within_tolerance),
        )
    conn.commit()


def test_empty_outcomes(conn) -> None:
    from pm_agent.graph.calibration import Calibration, compute_calibration

    assert compute_calibration(conn) == Calibration(None, None, 0)


def test_single_outcome(conn) -> None:
    from pm_agent.graph.calibration import Calibration, compute_calibration

    insert_outcome(conn, 3.0, True)
    assert compute_calibration(conn) == Calibration(3.0, 1.0, 1)


def test_seeded_demo_case(conn) -> None:
    """deltas +2.0, -7.0, +4.9 with within = True, False, True (docs/06)."""
    from pm_agent.graph.calibration import compute_calibration

    insert_outcome(conn, 2.0, True)
    insert_outcome(conn, -7.0, False)
    insert_outcome(conn, 4.9, True)

    cal = compute_calibration(conn)
    assert cal.n_resolved == 3
    assert cal.mean_abs_delta == pytest.approx((2.0 + 7.0 + 4.9) / 3)
    assert cal.hit_rate == pytest.approx(2 / 3)


def test_all_outside_tolerance(conn) -> None:
    from pm_agent.graph.calibration import compute_calibration

    insert_outcome(conn, 10.0, False)
    insert_outcome(conn, -20.0, False)

    cal = compute_calibration(conn)
    assert cal.n_resolved == 2
    assert cal.hit_rate == 0.0
    assert cal.mean_abs_delta == pytest.approx(15.0)

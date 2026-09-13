"""Phase 2 graph tests: grading, calibration, timeout, idempotency (docs/03 §Phase 2).

Fake PostHog/Slack/GitHub (monkeypatched in node namespaces); testcontainers
Postgres. Skips without Docker.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from pm_agent.integrations.base import FetchResult
from pm_agent.models import DataFlag, EdgeType, GraphEdge, GraphNode, NodeType, Severity, Source

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

REVIEW_DATE = date.today() - timedelta(days=1)
MEETING_DATE = REVIEW_DATE - timedelta(days=14)


@pytest.fixture(scope="module")
def pg_url():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
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
            migrate.run(conn)
        yield {"url": pg_url}
    finally:
        if original_url is not None:
            os.environ["DATABASE_URL"] = original_url
        else:
            os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()


def _setup_bet(migrated, predicted: float = 10.0) -> str:
    """SCHEDULED bet due for review + HYPOTHESIS/ASSUMPTION nodes + depends_on edge."""
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import store
    from pm_agent.state import create_initial_state

    bet_id = uuid.uuid4()
    hyp = GraphNode(
        bet_id=bet_id,
        type=NodeType.HYPOTHESIS,
        content="Guest checkout lifts conversion +10pp in 1 week",
        confidence=0.9,
        source="meeting:llm",
    )
    asm = GraphNode(
        bet_id=bet_id,
        type=NodeType.ASSUMPTION,
        content="Checkout friction is the blocker",
        source="meeting:llm",
    )
    edge = GraphEdge(bet_id=bet_id, from_node=hyp.id, to_node=asm.id, edge_type=EdgeType.DEPENDS_ON)
    with get_conn() as conn:
        store.create_bet(conn, bet_id, "doc-1", MEETING_DATE, "U000TEST")
        store.insert_nodes(conn, [hyp, asm])
        store.insert_edges(conn, [edge])
        store.update_bet_fields(
            conn,
            bet_id,
            metric_name="checkout_conversion",
            metric_query="SELECT lift FROM events",
            predicted_lift=predicted,
            review_date=REVIEW_DATE,
            status="SCHEDULED",
        )
    state = create_initial_state(str(bet_id), "doc-1", MEETING_DATE.isoformat())
    return str(bet_id), state


def _fake_posthog(monkeypatch, value=None, error=None) -> None:
    from pm_agent.pipeline.nodes import phase2

    monkeypatch.setattr(phase2, "run_hogql", lambda settings, query: FetchResult(value, error))


def _fake_reporters(monkeypatch) -> dict:
    from pm_agent.pipeline.nodes import phase2

    calls: dict[str, list[str]] = {"slack": [], "github": []}

    def fake_post_report(settings, text):
        calls["slack"].append(text)
        return FetchResult(None, None)

    def fake_comment(settings, issue_url, text):
        calls["github"].append(text)
        return FetchResult(None, None)

    monkeypatch.setattr(phase2, "post_report", fake_post_report)
    monkeypatch.setattr(phase2, "comment_on_issue", fake_comment)
    return calls


def _run_graph(migrated, state):
    from pm_agent.pipeline.phase2 import build_phase2_graph

    graph = build_phase2_graph()
    return graph.invoke(state, {"configurable": {"thread_id": f"{state['bet_id']}:review"}})


def _db(migrated, bet_id: str):
    from pm_agent.db.connection import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM bets WHERE id = %s", (uuid.UUID(bet_id),))
            status = cur.fetchone()[0]
            cur.execute(
                "SELECT actual_lift, predicted_lift, delta, within_tolerance"
                " FROM outcomes WHERE bet_id = %s",
                (uuid.UUID(bet_id),),
            )
            outcome = cur.fetchone()
            cur.execute("SELECT flags FROM bets WHERE id = %s", (uuid.UUID(bet_id),))
            flags = cur.fetchone()[0] or []
            cur.execute(
                "SELECT edge_type FROM graph_edges WHERE bet_id = %s"
                " AND edge_type IN ('supports','contradicts')",
                (uuid.UUID(bet_id),),
            )
            outcome_edges = [r[0] for r in cur.fetchall()]
    return status, outcome, flags, outcome_edges


def test_supports_case(migrated, monkeypatch):
    _fake_posthog(monkeypatch, value=12.0)
    reporters = _fake_reporters(monkeypatch)
    bet_id, state = _setup_bet(migrated, predicted=10.0)

    final = _run_graph(migrated, state)

    assert final["delta"] == 2.0
    status, outcome, flags, outcome_edges = _db(migrated, bet_id)
    assert status == "RESOLVED"
    assert outcome is not None
    assert float(outcome[2]) == 2.0
    assert outcome[3] is True  # within_tolerance
    assert outcome_edges == ["supports"]
    assert any("✅ supports" in t for t in reporters["slack"])


def test_contradicts_case(migrated, monkeypatch):
    _fake_posthog(monkeypatch, value=1.0)
    reporters = _fake_reporters(monkeypatch)
    bet_id, state = _setup_bet(migrated, predicted=10.0)

    final = _run_graph(migrated, state)

    assert final["delta"] == -9.0
    status, outcome, flags, outcome_edges = _db(migrated, bet_id)
    assert status == "RESOLVED"
    assert outcome[3] is False
    assert outcome_edges == ["contradicts"]
    assert any("❌ contradicts" in t for t in reporters["slack"])


def test_tolerance_boundary(migrated, monkeypatch):
    _fake_posthog(monkeypatch, value=15.0)  # delta == +5 == tol -> HIT
    _fake_reporters(monkeypatch)
    bet_id, state = _setup_bet(migrated, predicted=10.0)

    final = _run_graph(migrated, state)

    assert final["delta"] == 5.0
    status, outcome, flags, outcome_edges = _db(migrated, bet_id)
    assert status == "RESOLVED"
    assert outcome[3] is True  # abs(delta) == tol is a HIT
    assert outcome_edges == ["supports"]


def test_timeout_case(migrated, monkeypatch):
    _fake_posthog(monkeypatch, error=DataFlag(Source.POSTHOG, Severity.WARNING, "posthog_timeout"))
    reporters = _fake_reporters(monkeypatch)
    bet_id, state = _setup_bet(migrated, predicted=10.0)

    final = _run_graph(migrated, state)

    assert final["actual_lift"] is None
    assert any(f.message == "posthog_timeout" for f in final["flags"])
    status, outcome, flags, outcome_edges = _db(migrated, bet_id)
    assert status == "SCHEDULED"  # stays un-RESOLVED, retried next check
    assert outcome is None
    assert outcome_edges == []
    assert any(f["message"] == "posthog_timeout" for f in flags)  # persisted
    assert any("Could not fetch metric" in t for t in reporters["slack"])


def test_run_due_reviews_and_idempotent_rerun(migrated, monkeypatch):
    from pm_agent.db.connection import get_conn
    from pm_agent.pipeline.phase2 import run_due_reviews

    _fake_posthog(monkeypatch, value=12.0)
    _fake_reporters(monkeypatch)
    bet_id, state = _setup_bet(migrated, predicted=10.0)

    first = run_due_reviews()
    assert first["checked"] >= 1
    assert first["resolved"] >= 1

    status, outcome, flags, outcome_edges = _db(migrated, bet_id)
    assert status == "RESOLVED"

    second = run_due_reviews()  # resolved bets are no longer due
    assert second["resolved"] == 0  # this bet not re-resolved (timeout bet may remain due)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM outcomes WHERE bet_id = %s", (uuid.UUID(bet_id),))
            assert cur.fetchone()[0] == 1  # idempotent: exactly one outcomes row

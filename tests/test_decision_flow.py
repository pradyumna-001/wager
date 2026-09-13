"""Decision-node unit tests (docs/03 §docs_intake/confirm_decision/write_decision).

Fake integrations (monkeypatched in node namespaces); testcontainers Postgres
for status/flag persistence assertions. Skips without Docker.
"""

from __future__ import annotations

import uuid

import pytest

from pm_agent.errors import IntegrationError
from pm_agent.models import EdgeType, GraphNode, NodeType

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

MEETING_DATE = "2026-09-13"


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


def _make_state(migrated):
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import store
    from pm_agent.state import create_initial_state

    bet_id = str(uuid.uuid4())
    with get_conn() as conn:
        store.create_bet(conn, bet_id, "doc-1", MEETING_DATE, "U000TEST")
    state = create_initial_state(bet_id, "doc-1", MEETING_DATE)
    state["raw_transcript"] = "PM: onboarding takes 12 steps."
    state["hypotheses"] = [
        GraphNode(
            bet_id=uuid.UUID(bet_id),
            type=NodeType.HYPOTHESIS,
            content="Cutting steps lifts activation",
            source="meeting:llm",
        )
    ]
    return state


def _bet_status(migrated, bet_id: str) -> str:
    from pm_agent.db.connection import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM bets WHERE id = %s", (uuid.UUID(bet_id),))
            return cur.fetchone()[0]


def _bet_flags(migrated, bet_id: str) -> list[dict]:
    from pm_agent.db.connection import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT flags FROM bets WHERE id = %s", (uuid.UUID(bet_id),))
            return cur.fetchone()[0] or []


def test_docs_intake_success(migrated, monkeypatch):
    from pm_agent.integrations.base import FetchResult
    from pm_agent.pipeline.nodes import intake

    monkeypatch.setattr(
        intake, "fetch_transcript", lambda settings, doc_id: FetchResult("transcript text", None)
    )
    state = _make_state(migrated)

    delta = intake.docs_intake(state)

    assert delta["raw_transcript"] == "transcript text"
    assert _bet_status(migrated, state["bet_id"]) == "EXTRACTING"


def test_docs_intake_failure_raises(migrated, monkeypatch):
    from pm_agent.integrations.base import FetchResult
    from pm_agent.models import DataFlag, Severity, Source
    from pm_agent.pipeline.nodes import intake

    error = DataFlag(Source.GDOCS, Severity.CRITICAL, "gdocs_fetch_failed: 500")
    monkeypatch.setattr(
        intake, "fetch_transcript", lambda settings, doc_id: FetchResult(None, error)
    )
    state = _make_state(migrated)

    with pytest.raises(IntegrationError):
        intake.docs_intake(state)


def test_confirm_decision_error(migrated, monkeypatch):
    from pm_agent.integrations.base import FetchResult
    from pm_agent.models import DataFlag, Severity, Source
    from pm_agent.pipeline.nodes import decision

    error = DataFlag(Source.SLACK, Severity.CRITICAL, "slack_post_failed: down")
    monkeypatch.setattr(
        decision,
        "post_confirm",
        lambda settings, bet_id, options, i, summary: FetchResult(None, error),
    )
    state = _make_state(migrated)
    state["options"] = [
        GraphNode(
            bet_id=uuid.UUID(state["bet_id"]), type=NodeType.OPTION, content="A", source="llm"
        )
    ]

    with pytest.raises(IntegrationError):
        decision.confirm_decision(state)

    assert _bet_status(migrated, state["bet_id"]) == "PENDING_CONFIRMATION"
    flags = _bet_flags(migrated, state["bet_id"])
    assert any(f["message"].startswith("slack_confirm_failed") for f in flags)


def test_write_decision_writes_decision(migrated):
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import store
    from pm_agent.pipeline.nodes import decision

    state = _make_state(migrated)
    option = GraphNode(
        bet_id=uuid.UUID(state["bet_id"]), type=NodeType.OPTION, content="Cut steps", source="llm"
    )
    with get_conn() as conn:
        store.insert_nodes(conn, [option])
    state["options"] = [option]
    state["chosen_option_id"] = str(option.id)

    delta = decision.write_decision(state)

    assert delta["decision_node_id"] is not None
    with get_conn() as conn:
        nodes, edges = store.get_bet_graph(conn, state["bet_id"])
    assert any(n.type == NodeType.DECISION for n in nodes)
    assert any(e.edge_type == EdgeType.LEADS_TO for e in edges)
    assert _bet_status(migrated, state["bet_id"]) == "CONFIRMED"


def test_write_decision_missing_option(migrated):
    from pm_agent.pipeline.nodes import decision

    state = _make_state(migrated)
    state["chosen_option_id"] = str(uuid.uuid4())
    state["options"] = []

    with pytest.raises(IntegrationError):
        decision.write_decision(state)

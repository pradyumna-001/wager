"""Phase 1 graph tests: wiring + interrupt/resume (docs/03, ADR 0002/0003).

Fake LLM + fake integrations (monkeypatched in node namespaces); in-memory
checkpointer for unit/resume tests, PostgresSaver via testcontainers for the
resume-after-restart persistence proof. Skips without Docker.
"""

from __future__ import annotations

import uuid

import pytest

from pm_agent.models import EdgeType, NodeType

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


def _fake_llm(monkeypatch) -> None:
    from pm_agent.models import (
        DerivedItem,
        DerivedReasoning,
        ExtractedFacts,
        FactItem,
        HypothesisItem,
        OptionItem,
    )
    from pm_agent.pipeline.nodes import extraction

    facts = ExtractedFacts(
        facts=[
            FactItem(content="Onboarding takes 12 steps", speaker="PM"),
            FactItem(content="We lose users at step 9", speaker="PM"),
        ]
    )
    reasoning = DerivedReasoning(
        assumptions=[DerivedItem(content="Steps 9-12 are friction", derived_from_fact_ids=[0, 1])],
        hypotheses=[
            HypothesisItem(
                content="Cutting steps 9-12 lifts activation",
                confidence=0.8,
                derived_from_fact_ids=[1],
                depends_on_assumption_idxs=[0],
                metric_name="signup_completion_rate",
                metric_query="SELECT lift FROM events",
                predicted_lift=15.0,
                measurement_days=14,
            )
        ],
        options=[
            OptionItem(content="Cut steps 9-12", derived_from_fact_ids=[0]),
            OptionItem(content="Keep onboarding", derived_from_fact_ids=[1]),
        ],
        proposed_option_index=0,
    )

    def fake_call_llm(settings, system, user, schema):
        if schema is ExtractedFacts:
            return facts
        return reasoning

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)


def _fake_integrations(monkeypatch, *, slack_error=None, github_error=None, gcal_error=None):
    from pm_agent.integrations.base import FetchResult
    from pm_agent.pipeline.nodes import decision, intake, phase1_effects

    def fake_fetch(settings, doc_id):
        return FetchResult("PM: onboarding takes 12 steps.", None)

    def fake_post_confirm(settings, bet_id, options, proposed_index, hypothesis_summary):
        return FetchResult(None, slack_error)

    def fake_issue(settings, bet_id, title, body):
        return FetchResult("https://github.com/o/r/issues/1", github_error)

    def fake_event(settings, review_date, title, description):
        return FetchResult("evt-1", gcal_error)

    monkeypatch.setattr(intake, "fetch_transcript", fake_fetch)
    monkeypatch.setattr(decision, "post_confirm", fake_post_confirm)
    monkeypatch.setattr(phase1_effects, "create_or_comment_issue", fake_issue)
    monkeypatch.setattr(phase1_effects, "create_review_event", fake_event)


def _make_state(migrated):
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import store
    from pm_agent.state import create_initial_state

    bet_id = str(uuid.uuid4())
    with get_conn() as conn:
        store.create_bet(conn, bet_id, "doc-1", MEETING_DATE, "U000TEST")
    state = create_initial_state(bet_id, "doc-1", MEETING_DATE)
    return state, {"configurable": {"thread_id": bet_id}}


def _db(migrated, state):
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import store

    with get_conn() as conn:
        nodes, edges = store.get_bet_graph(conn, state["bet_id"])
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, flags FROM bets WHERE id = %s", (uuid.UUID(state["bet_id"]),)
            )
            status, flags = cur.fetchone()
    return nodes, edges, status, flags or []


def test_full_happy_path(migrated, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from pm_agent.pipeline.phase1 import build_phase1_graph

    _fake_llm(monkeypatch)
    _fake_integrations(monkeypatch)
    state, config = _make_state(migrated)
    graph = build_phase1_graph(MemorySaver())

    paused = graph.invoke(state, config)
    assert "__interrupt__" in paused

    final = graph.invoke(Command(resume={"chosen_option_index": 0}), config)

    assert final["chosen_option_id"] is not None
    assert final["decision_node_id"] is not None
    assert final["github_issue_url"] == "https://github.com/o/r/issues/1"
    assert final["calendar_event_id"] == "evt-1"
    nodes, edges, status, _ = _db(migrated, state)
    assert any(n.type == NodeType.DECISION for n in nodes)
    assert any(e.edge_type == EdgeType.LEADS_TO for e in edges)
    assert status == "SCHEDULED"


def test_interrupt_fires_no_decision(migrated, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver

    from pm_agent.pipeline.phase1 import build_phase1_graph

    _fake_llm(monkeypatch)
    _fake_integrations(monkeypatch)
    state, config = _make_state(migrated)
    graph = build_phase1_graph(MemorySaver())

    paused = graph.invoke(state, config)

    payload = paused["__interrupt__"][0].value
    assert payload["options"] == ["Cut steps 9-12", "Keep onboarding"]
    assert payload["proposed_index"] == 0
    nodes, edges, status, _ = _db(migrated, state)
    assert all(n.type != NodeType.DECISION for n in nodes)
    assert all(e.edge_type != EdgeType.LEADS_TO for e in edges)
    assert status == "PENDING_CONFIRMATION"


def test_resume_invalid_index(migrated, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from pm_agent.errors import IntegrationError
    from pm_agent.pipeline.phase1 import build_phase1_graph

    _fake_llm(monkeypatch)
    _fake_integrations(monkeypatch)
    state, config = _make_state(migrated)
    graph = build_phase1_graph(MemorySaver())

    graph.invoke(state, config)
    with pytest.raises(IntegrationError):
        graph.invoke(Command(resume={"chosen_option_index": 99}), config)


def test_resume_after_new_graph_instance(migrated, monkeypatch, pg_url):
    from langgraph.types import Command

    from pm_agent.pipeline.phase1 import build_phase1_graph

    _fake_llm(monkeypatch)
    _fake_integrations(monkeypatch)
    state, config = _make_state(migrated)

    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(pg_url) as saver1:
        saver1.setup()
        graph1 = build_phase1_graph(saver1)
        paused = graph1.invoke(state, config)
        assert "__interrupt__" in paused

    with PostgresSaver.from_conn_string(pg_url) as saver2:
        saver2.setup()
        graph2 = build_phase1_graph(saver2)
        final = graph2.invoke(Command(resume={"chosen_option_index": 0}), config)
        assert final["decision_node_id"] is not None
        assert final["calendar_event_id"] == "evt-1"


def test_github_failure_flag(migrated, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from pm_agent.models import DataFlag, Severity, Source
    from pm_agent.pipeline.phase1 import build_phase1_graph

    _fake_llm(monkeypatch)
    _fake_integrations(
        monkeypatch,
        github_error=DataFlag(Source.GITHUB, Severity.CRITICAL, "github_error: 500"),
    )
    state, config = _make_state(migrated)
    graph = build_phase1_graph(MemorySaver())

    paused = graph.invoke(state, config)
    assert "__interrupt__" in paused
    final = graph.invoke(Command(resume={"chosen_option_index": 0}), config)

    assert final["github_issue_url"] is None
    assert any(f.message.startswith("github_write_failed") for f in final["flags"])
    nodes, edges, status, flags = _db(migrated, state)
    assert status == "SCHEDULED"
    assert any(f["message"].startswith("github_write_failed") for f in flags)
    assert final["calendar_event_id"] == "evt-1"

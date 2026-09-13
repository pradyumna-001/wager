"""Extraction node tests: fake LLM + testcontainers Postgres (docs/03, ADR 0002).

Skipped gracefully when Docker is unavailable. Exercises node/edge shapes in
DB, the empty-extraction guardrail, hypothesis collapsing, transaction
atomicity, and that no DECISION node is ever written (ADR 0002).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from pm_agent.errors import EmptyExtractionError, PipelineError
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


def _setup_bet(migrated, fact_contents: list[str], ghost_facts: int = 0):
    """Create a bets row + FACT nodes; return (state, facts). Ghost facts are
    appended to state but NOT inserted (for the atomicity test)."""
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import store
    from pm_agent.state import create_initial_state

    bet_id = uuid.uuid4()
    facts = [
        GraphNode(
            bet_id=bet_id,
            type=NodeType.FACT,
            content=c,
            source=f"meeting:{MEETING_DATE}:speaker=PM",
        )
        for c in fact_contents
    ]
    with get_conn() as conn:
        store.create_bet(conn, bet_id, "doc-1", MEETING_DATE, "U000TEST")
        store.insert_nodes(conn, facts)
    state = create_initial_state(str(bet_id), "doc-1", MEETING_DATE)
    state["raw_transcript"] = "PM: onboarding takes 12 steps; we lose users at step 9."
    state["facts"] = list(facts)
    ghosts = [
        GraphNode(
            bet_id=bet_id,
            type=NodeType.FACT,
            content=f"ghost-{i}",
            source=f"meeting:{MEETING_DATE}:speaker=PM",
        )
        for i in range(ghost_facts)
    ]
    state["facts"].extend(ghosts)
    return state, facts


def _fake_llm(monkeypatch, payloads: list):
    from pm_agent.pipeline.nodes import extraction

    def fake_call_llm(settings, system, user, schema):
        return payloads.pop(0)

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)


def test_extract_facts_writes_fact_nodes(migrated, monkeypatch):
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import store
    from pm_agent.models import ExtractedFacts, FactItem
    from pm_agent.pipeline.nodes import extraction

    state, _ = _setup_bet(migrated, [])
    _fake_llm(
        monkeypatch,
        [
            ExtractedFacts(
                facts=[
                    FactItem(content="Onboarding takes 12 steps", speaker="PM"),
                    FactItem(content="We lose users at step 9", speaker="PM"),
                ]
            )
        ],
    )

    delta = extraction.extract_facts(state)

    assert len(delta["facts"]) == 2
    assert all(f.source == f"meeting:{MEETING_DATE}:speaker=PM" for f in delta["facts"])
    with get_conn() as conn:
        nodes, edges = store.get_bet_graph(conn, state["bet_id"])
    assert len(nodes) == 2
    assert edges == []
    assert all(n.type == NodeType.FACT for n in nodes)
    assert {n.content for n in nodes} == {"Onboarding takes 12 steps", "We lose users at step 9"}


def test_extract_facts_empty_raises(migrated, monkeypatch):
    from pm_agent.db.connection import get_conn
    from pm_agent.models import ExtractedFacts
    from pm_agent.pipeline.nodes import extraction

    state, _ = _setup_bet(migrated, [])
    _fake_llm(monkeypatch, [ExtractedFacts(facts=[])])

    with pytest.raises(EmptyExtractionError):
        extraction.extract_facts(state)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT flags FROM bets WHERE id = %s", (uuid.UUID(state["bet_id"]),))
            flags = cur.fetchone()[0]
    assert any(f["message"] == "extract_facts_empty" for f in flags)


def test_derive_reasoning_builds_nodes_edges(migrated, monkeypatch):
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import store
    from pm_agent.models import (
        DerivedItem,
        DerivedReasoning,
        HypothesisItem,
        OptionItem,
    )
    from pm_agent.pipeline.nodes import extraction

    state, facts = _setup_bet(migrated, ["Onboarding takes 12 steps", "We lose users at step 9"])
    _fake_llm(
        monkeypatch,
        [
            DerivedReasoning(
                assumptions=[
                    DerivedItem(content="Steps 9-12 are friction", derived_from_fact_ids=[0, 1])
                ],
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
        ],
    )

    delta = extraction.derive_reasoning(state)

    assert delta["proposed_option_id"] == str(delta["options"][0].id)
    assert delta["metric_name"] == "signup_completion_rate"
    assert delta["predicted_lift"] == 15.0
    expected_review = (date.fromisoformat(MEETING_DATE) + timedelta(days=14)).isoformat()
    assert delta["review_date"] == expected_review
    assert len(delta["assumptions"]) == 1 and len(delta["options"]) == 2

    with get_conn() as conn:
        nodes, edges = store.get_bet_graph(conn, state["bet_id"])
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metric_name, predicted_lift, review_date FROM bets WHERE id = %s",
                (uuid.UUID(state["bet_id"]),),
            )
            metric_name, predicted_lift, review_date = cur.fetchone()
    types = {n.type for n in nodes}
    assert NodeType.ASSUMPTION in types
    assert NodeType.HYPOTHESIS in types
    assert NodeType.OPTION in types
    edge_types = {e.edge_type for e in edges}
    assert EdgeType.DERIVED_FROM in edge_types
    assert EdgeType.DEPENDS_ON in edge_types
    hyp_node = next(n for n in nodes if n.type == NodeType.HYPOTHESIS)
    dep = next(e for e in edges if e.edge_type == EdgeType.DEPENDS_ON)
    assert dep.from_node == hyp_node.id
    assert dep.to_node == delta["assumptions"][0].id
    assert metric_name == "signup_completion_rate"
    assert float(predicted_lift) == 15.0
    assert date.fromisoformat(str(review_date)) == date.fromisoformat(expected_review)


def test_derive_reasoning_collapses_multiple_hypotheses(migrated, monkeypatch):
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import store
    from pm_agent.models import (
        DerivedItem,
        DerivedReasoning,
        HypothesisItem,
        OptionItem,
    )
    from pm_agent.pipeline.nodes import extraction

    state, facts = _setup_bet(migrated, ["Onboarding takes 12 steps"])
    first = HypothesisItem(
        content="First hypothesis",
        confidence=0.9,
        derived_from_fact_ids=[0],
        depends_on_assumption_idxs=[],
        metric_name="m1",
        metric_query="SELECT 1",
        predicted_lift=10.0,
        measurement_days=7,
    )
    second = HypothesisItem(
        content="Second hypothesis",
        confidence=0.5,
        derived_from_fact_ids=[0],
        depends_on_assumption_idxs=[],
        metric_name="m2",
        metric_query="SELECT 2",
        predicted_lift=20.0,
        measurement_days=7,
    )
    _fake_llm(
        monkeypatch,
        [
            DerivedReasoning(
                assumptions=[DerivedItem(content="A", derived_from_fact_ids=[0])],
                hypotheses=[first, second],
                options=[OptionItem(content="O", derived_from_fact_ids=[0])],
                proposed_option_index=0,
            )
        ],
    )

    delta = extraction.derive_reasoning(state)

    assert delta["flags"] and delta["flags"][0].message == "multiple_hypotheses_collapsed"
    assert len(delta["hypotheses"]) == 1
    assert delta["hypotheses"][0].content == "First hypothesis"
    with get_conn() as conn:
        nodes, _ = store.get_bet_graph(conn, state["bet_id"])
    assert sum(1 for n in nodes if n.type == NodeType.HYPOTHESIS) == 1


def test_no_decision_node_after_extraction(migrated, monkeypatch):
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import store
    from pm_agent.models import (
        DerivedItem,
        DerivedReasoning,
        ExtractedFacts,
        FactItem,
        HypothesisItem,
        OptionItem,
    )
    from pm_agent.pipeline.nodes import extraction

    state, _ = _setup_bet(migrated, [])
    _fake_llm(
        monkeypatch,
        [
            ExtractedFacts(facts=[FactItem(content="Onboarding takes 12 steps", speaker="PM")]),
            DerivedReasoning(
                assumptions=[DerivedItem(content="A", derived_from_fact_ids=[0])],
                hypotheses=[
                    HypothesisItem(
                        content="H",
                        confidence=0.8,
                        derived_from_fact_ids=[0],
                        depends_on_assumption_idxs=[0],
                        metric_name="m",
                        metric_query="SELECT 1",
                        predicted_lift=10.0,
                        measurement_days=7,
                    )
                ],
                options=[OptionItem(content="O", derived_from_fact_ids=[0])],
                proposed_option_index=0,
            ),
        ],
    )

    extract_facts_delta = extraction.extract_facts(state)
    state["facts"] = extract_facts_delta["facts"]
    derive_delta = extraction.derive_reasoning(state)

    assert derive_delta["proposed_option_id"] is not None
    with get_conn() as conn:
        nodes, edges = store.get_bet_graph(conn, state["bet_id"])
    assert all(n.type != NodeType.DECISION for n in nodes)
    assert all(e.edge_type != EdgeType.LEADS_TO for e in edges)


def test_derive_transaction_atomicity(migrated, monkeypatch):
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import store
    from pm_agent.models import (
        DerivedItem,
        DerivedReasoning,
        HypothesisItem,
        OptionItem,
    )
    from pm_agent.pipeline.nodes import extraction

    state, facts = _setup_bet(migrated, ["Onboarding takes 12 steps"], ghost_facts=1)
    _fake_llm(
        monkeypatch,
        [
            DerivedReasoning(
                assumptions=[DerivedItem(content="A", derived_from_fact_ids=[1])],
                hypotheses=[
                    HypothesisItem(
                        content="H",
                        confidence=0.8,
                        derived_from_fact_ids=[1],
                        depends_on_assumption_idxs=[0],
                        metric_name="m",
                        metric_query="SELECT 1",
                        predicted_lift=10.0,
                        measurement_days=7,
                    )
                ],
                options=[OptionItem(content="O", derived_from_fact_ids=[0])],
                proposed_option_index=0,
            )
        ],
    )

    with pytest.raises(PipelineError):
        extraction.derive_reasoning(state)

    with get_conn() as conn:
        nodes, edges = store.get_bet_graph(conn, state["bet_id"])
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metric_name, review_date FROM bets WHERE id = %s",
                (uuid.UUID(state["bet_id"]),),
            )
            metric_name, review_date = cur.fetchone()
    assert nodes == facts
    assert edges == []
    assert metric_name is None
    assert review_date is None

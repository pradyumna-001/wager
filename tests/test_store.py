"""Graph store tests against a testcontainers Postgres.

Skipped gracefully when Docker is unavailable. Exercises happy paths, every
invariant violation (ADR 0002 attestation, referential integrity, idempotent
resolve), and the one-transaction semantics of node+edge writes.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

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

TODAY = date.today()
SOURCE_TAG = "meeting:2026-09-13:speaker=PM"


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
            migrate.run(conn)
        yield {"url": pg_url}
    finally:
        if original_url is not None:
            os.environ["DATABASE_URL"] = original_url
        else:
            os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()


# --- helpers ---


def _node(bet_id, type_, content, **kwargs):
    from pm_agent.models import GraphNode, NodeType

    return GraphNode(
        bet_id=bet_id, type=NodeType(type_), content=content, source=SOURCE_TAG, **kwargs
    )


def _graph(bet_id):
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import get_bet_graph

    with get_conn() as conn:
        return get_bet_graph(conn, bet_id)


def _edge(bet_id, from_node, to_node, edge_type):
    from pm_agent.models import GraphEdge

    return GraphEdge(bet_id=bet_id, from_node=from_node, to_node=to_node, edge_type=edge_type)


def _flag(source, severity, message):
    from pm_agent.models import DataFlag

    return DataFlag(source=source, severity=severity, message=message)


@pytest.fixture()
def bet_with_options(migrated):
    """One bet with a FACT, a HYPOTHESIS (confidence 0.7) and two OPTION nodes."""
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import create_bet, insert_nodes
    from pm_agent.models import NodeType

    bet_id = uuid.uuid4()
    fact = _node(bet_id, NodeType.FACT, "Activation dropped 12% last month")
    hyp = _node(
        bet_id,
        NodeType.HYPOTHESIS,
        "Onboarding checklist lifts activations",
        confidence=0.7,
    )
    opt_a = _node(bet_id, NodeType.OPTION, "Ship onboarding checklist")
    opt_b = _node(bet_id, NodeType.OPTION, "Do nothing")
    with get_conn() as conn:
        create_bet(conn, bet_id, "doc-m12", TODAY.isoformat(), "U000")
        insert_nodes(conn, [fact, hyp, opt_a, opt_b])
    return {"bet_id": bet_id, "fact": fact, "hypothesis": hyp, "options": [opt_a, opt_b]}


def _new_bet_with_fact(bet_id):
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import create_bet, insert_nodes
    from pm_agent.models import NodeType

    fact = _node(bet_id, NodeType.FACT, "Churn up 4 points")
    with get_conn() as conn:
        create_bet(conn, bet_id, "doc-x", TODAY.isoformat(), "U000")
        insert_nodes(conn, [fact])
    return fact


# --- insert_nodes / insert_edges ---


def test_insert_nodes_and_edges_roundtrip(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import insert_edges
    from pm_agent.models import EdgeType, NodeType

    b = bet_with_options
    with get_conn() as conn:
        insert_edges(
            conn, [_edge(b["bet_id"], b["hypothesis"].id, b["fact"].id, EdgeType.DERIVED_FROM)]
        )
    nodes, edges = _graph(b["bet_id"])
    assert len(nodes) == 4
    assert {n.type for n in nodes} == {NodeType.FACT, NodeType.HYPOTHESIS, NodeType.OPTION}
    assert len(edges) == 1
    assert edges[0].edge_type == EdgeType.DERIVED_FROM
    assert edges[0].from_node == b["hypothesis"].id
    assert edges[0].to_node == b["fact"].id


def test_insert_edges_fk_violation_rolls_back_whole_txn(bet_with_options) -> None:
    """A failing edge insert must roll the whole transaction back (one-txn invariant)."""
    from pm_agent.db.connection import get_conn
    from pm_agent.errors import PipelineError
    from pm_agent.graph import insert_edges, insert_nodes
    from pm_agent.models import EdgeType

    bet_id = uuid.uuid4()
    _new_bet_with_fact(bet_id)  # existing bet, already committed
    fresh_node = _node(bet_id, "FACT", "second fact")
    bad_edge = _edge(bet_id, fresh_node.id, uuid.uuid4(), EdgeType.DERIVED_FROM)  # missing node
    with pytest.raises(PipelineError):
        with get_conn() as conn:
            insert_nodes(conn, [fresh_node])
            insert_edges(conn, [bad_edge])
    # rollback: even the node insert must not have persisted
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM graph_nodes WHERE bet_id = %s", (bet_id,))
        assert cur.fetchone()[0] == 1  # only the pre-existing fact


def test_insert_nodes_missing_bet_raises(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.errors import PipelineError
    from pm_agent.graph import insert_nodes
    from pm_agent.models import NodeType

    orphan = _node(uuid.uuid4(), NodeType.FACT, "orphan fact")
    with pytest.raises(PipelineError):
        with get_conn() as conn:
            insert_nodes(conn, [orphan])


def test_get_bet_graph_isolates_bets(bet_with_options) -> None:
    other_id = uuid.uuid4()
    _new_bet_with_fact(other_id)
    nodes, edges = _graph(bet_with_options["bet_id"])
    assert all(n.bet_id == bet_with_options["bet_id"] for n in nodes)
    assert all(e.bet_id == bet_with_options["bet_id"] for e in edges)
    other_nodes, other_edges = _graph(other_id)
    assert len(other_nodes) == 1
    assert other_edges == []


# --- create_bet / update_bet_status / update_bet_fields / append_flag ---


def test_create_bet_defaults_to_extracting(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn

    bet_id = bet_with_options["bet_id"]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT status, doc_id, pm_slack_id FROM bets WHERE id = %s", (bet_id,))
        status, doc_id, slack_id = cur.fetchone()
    assert status == "EXTRACTING"
    assert doc_id == "doc-m12"
    assert slack_id == "U000"


def test_update_bet_status_roundtrip_and_invalid(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.errors import PipelineError
    from pm_agent.graph import update_bet_status

    bet_id = bet_with_options["bet_id"]
    with get_conn() as conn:
        update_bet_status(conn, bet_id, "CONFIRMED")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM bets WHERE id = %s", (bet_id,))
        assert cur.fetchone()[0] == "CONFIRMED"
    with pytest.raises(PipelineError):
        with get_conn() as conn:
            update_bet_status(conn, bet_id, "NOT_A_STATUS")


def test_update_bet_fields_roundtrip_unknown_refused(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.errors import PipelineError
    from pm_agent.graph import update_bet_fields

    bet_id = bet_with_options["bet_id"]
    with get_conn() as conn:
        update_bet_fields(
            conn,
            bet_id,
            metric_name="signup_completion_rate",
            metric_query="SELECT count(*) FROM events",
            predicted_lift=15.0,
            review_date=(TODAY + timedelta(days=14)).isoformat(),
        )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT metric_name, predicted_lift, review_date FROM bets WHERE id = %s",
            (bet_id,),
        )
        name, lift, review = cur.fetchone()
    assert name == "signup_completion_rate"
    assert lift == 15.0
    assert review == TODAY + timedelta(days=14)
    with pytest.raises(PipelineError):
        with get_conn() as conn:
            update_bet_fields(conn, bet_id, flags="nope")


def test_append_flag_persists_jsonb(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import append_flag
    from pm_agent.models import Severity, Source

    bet_id = bet_with_options["bet_id"]
    with get_conn() as conn:
        append_flag(conn, bet_id, _flag(Source.GITHUB, Severity.WARNING, "issue create failed"))
        append_flag(conn, bet_id, _flag(Source.DB, Severity.INFO, "second"))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT flags FROM bets WHERE id = %s", (bet_id,))
        flags = cur.fetchone()[0]
    assert [f["message"] for f in flags] == ["issue create failed", "second"]
    assert flags[0]["source"] == "github"
    assert flags[0]["severity"] == "WARNING"


# --- write_decision (ADR 0002) ---


def test_write_decision_creates_node_and_edge(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import write_decision
    from pm_agent.models import EdgeType, NodeType

    b = bet_with_options
    with get_conn() as conn:
        decision_id = write_decision(conn, b["bet_id"], b["options"][0].id, "Ship the checklist")
    nodes, edges = _graph(b["bet_id"])
    decisions = [n for n in nodes if n.type == NodeType.DECISION]
    assert len(decisions) == 1
    assert decisions[0].id == decision_id
    assert decisions[0].content == "Ship the checklist"
    leads = [e for e in edges if e.edge_type == EdgeType.LEADS_TO]
    assert len(leads) == 1
    assert leads[0].from_node == b["options"][0].id
    assert leads[0].to_node == decision_id


def test_write_decision_refuses_second_decision(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.errors import PipelineError
    from pm_agent.graph import write_decision

    b = bet_with_options
    with get_conn() as conn:
        write_decision(conn, b["bet_id"], b["options"][0].id, "first decision")
    with pytest.raises(PipelineError):
        with get_conn() as conn:
            write_decision(conn, b["bet_id"], b["options"][1].id, "second decision")


def test_write_decision_refuses_unknown_option(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.errors import PipelineError
    from pm_agent.graph import write_decision

    with pytest.raises(PipelineError):
        with get_conn() as conn:
            write_decision(conn, bet_with_options["bet_id"], uuid.uuid4(), "ghost")


# --- write_outcome (idempotent resolve) ---


def test_write_outcome_resolves_everything(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import update_bet_status, write_decision, write_outcome
    from pm_agent.models import EdgeType, NodeStatus, NodeType

    b = bet_with_options
    with get_conn() as conn:
        update_bet_status(conn, b["bet_id"], "CONFIRMED")
        write_decision(conn, b["bet_id"], b["options"][0].id, "Ship the checklist")
        write_outcome(
            conn,
            b["bet_id"],
            b["hypothesis"].id,
            actual=17.0,
            predicted=15.0,
            within=True,
            decision_node="Graded via PostHog review",
        )
    nodes, edges = _graph(b["bet_id"])
    hyp = next(n for n in nodes if n.id == b["hypothesis"].id)
    assert hyp.status == NodeStatus.RESOLVED
    outcome_nodes = [
        n for n in nodes if n.type == NodeType.DECISION and n.content == "Graded via PostHog review"
    ]
    assert len(outcome_nodes) == 1
    supports = [e for e in edges if e.edge_type == EdgeType.SUPPORTS]
    assert len(supports) == 1
    assert supports[0].from_node == outcome_nodes[0].id
    assert supports[0].to_node == b["hypothesis"].id
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT actual_lift, predicted_lift, delta, within_tolerance FROM outcomes"
            " WHERE bet_id = %s",
            (b["bet_id"],),
        )
        actual, predicted, delta, within = cur.fetchone()
        cur.execute("SELECT status FROM bets WHERE id = %s", (b["bet_id"],))
        bet_status = cur.fetchone()[0]
    assert (actual, predicted, delta, within) == (17.0, 15.0, 2.0, True)
    assert bet_status == "RESOLVED"


def test_write_outcome_contradicts_when_out_of_tolerance(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import write_decision, write_outcome
    from pm_agent.models import EdgeType

    b = bet_with_options
    with get_conn() as conn:
        write_decision(conn, b["bet_id"], b["options"][0].id, "Ship the checklist")
        write_outcome(
            conn,
            b["bet_id"],
            b["hypothesis"].id,
            actual=1.0,
            predicted=15.0,
            within=False,
            decision_node="Graded via PostHog review",
        )
    _, edges = _graph(b["bet_id"])
    contradicts = [e for e in edges if e.edge_type == EdgeType.CONTRADICTS]
    assert len(contradicts) == 1


def test_write_outcome_idempotent_double_resolve(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import write_decision, write_outcome
    from pm_agent.models import EdgeType

    b = bet_with_options
    with get_conn() as conn:
        write_decision(conn, b["bet_id"], b["options"][0].id, "Ship the checklist")
        write_outcome(conn, b["bet_id"], b["hypothesis"].id, 12.0, 15.0, False, "graded")
        write_outcome(conn, b["bet_id"], b["hypothesis"].id, 99.0, 15.0, True, "graded again")
    nodes, edges = _graph(b["bet_id"])
    outcome_nodes = [n for n in nodes if n.content in {"graded", "graded again"}]
    assert len(outcome_nodes) == 1  # second call was a no-op
    outcome_edges = [e for e in edges if e.edge_type in {EdgeType.SUPPORTS, EdgeType.CONTRADICTS}]
    assert len(outcome_edges) == 1
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM outcomes WHERE bet_id = %s", (b["bet_id"],))
        assert cur.fetchone()[0] == 1


def test_write_outcome_refuses_unknown_hypothesis(bet_with_options) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.errors import PipelineError
    from pm_agent.graph import write_decision, write_outcome

    b = bet_with_options
    with pytest.raises(PipelineError):
        with get_conn() as conn:
            write_decision(conn, b["bet_id"], b["options"][0].id, "Ship the checklist")
            write_outcome(conn, b["bet_id"], uuid.uuid4(), 10.0, 15.0, True, "graded")


# --- due_bets ---


def test_due_bets_filters_status_and_date(migrated) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import create_bet, due_bets, update_bet_fields, update_bet_status

    # 2-day offsets: the DB server's CURRENT_DATE is UTC and may drift one day
    # from local date.today() (machine runs UTC-3), so stay clear of the boundary.
    past = (TODAY - timedelta(days=2)).isoformat()
    future = (TODAY + timedelta(days=2)).isoformat()

    due_confirmed = uuid.uuid4()
    future_confirmed = uuid.uuid4()
    due_scheduled = uuid.uuid4()
    extracting = uuid.uuid4()
    resolved = uuid.uuid4()

    with get_conn() as conn:
        for bet_id in (due_confirmed, future_confirmed, due_scheduled, extracting, resolved):
            create_bet(conn, bet_id, "doc-due", TODAY.isoformat(), "U000")
        update_bet_status(conn, due_confirmed, "CONFIRMED")
        update_bet_fields(conn, due_confirmed, review_date=past)
        update_bet_status(conn, future_confirmed, "CONFIRMED")
        update_bet_fields(conn, future_confirmed, review_date=future)
        update_bet_status(conn, due_scheduled, "SCHEDULED")
        update_bet_fields(conn, due_scheduled, review_date=past)
        update_bet_status(conn, resolved, "RESOLVED")
        update_bet_fields(conn, resolved, review_date=past)

    with get_conn() as conn:
        due = {b.id for b in due_bets(conn, demo_mode=False)}
        demo = {b.id for b in due_bets(conn, demo_mode=True)}

    # subset assertions: the module-scoped container also holds bets from other tests
    assert {due_confirmed, due_scheduled} <= due
    assert future_confirmed not in due
    assert extracting not in due
    assert resolved not in due
    # demo_mode ignores the review date, so the future-dated bet becomes due
    assert {due_confirmed, future_confirmed, due_scheduled} <= demo


def test_due_bets_deserializes_flags(migrated) -> None:
    from pm_agent.db.connection import get_conn
    from pm_agent.graph import append_flag, create_bet, due_bets, update_bet_status
    from pm_agent.models import Severity, Source

    bet_id = uuid.uuid4()
    with get_conn() as conn:
        create_bet(conn, bet_id, "doc-flags", TODAY.isoformat(), "U000")
        update_bet_status(conn, bet_id, "CONFIRMED")
        append_flag(conn, bet_id, _flag(Source.GITHUB, Severity.CRITICAL, "slack write failed"))
    with get_conn() as conn:
        bets = due_bets(conn, demo_mode=True)
    match = next(b for b in bets if b.id == bet_id)
    assert len(match.flags) == 1
    assert match.flags[0].source == Source.GITHUB
    assert match.flags[0].severity == Severity.CRITICAL
    assert match.flags[0].message == "slack write failed"

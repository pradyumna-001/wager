"""Graph + bet persistence in Postgres (psycopg3, sync).

Postgres is the only source of truth (AGENTS.md invariant 1): graph records are
written here BEFORE any external side effect. Store functions take a connection
and NEVER commit — callers group writes into ONE transaction via ``get_conn()``
(psycopg commits on successful context exit, rolls back on exception).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from uuid import UUID

from psycopg import Connection, sql
from pydantic import BaseModel, Field

from pm_agent.errors import PipelineError
from pm_agent.models import (
    DataFlag,
    EdgeType,
    GraphEdge,
    GraphNode,
    NodeStatus,
    NodeType,
    Severity,
    Source,
)

_BET_STATUSES = (
    "EXTRACTING",
    "PENDING_CONFIRMATION",
    "CONFIRMED",
    "SCHEDULED",
    "RESOLVED",
    "FLAGGED",
)

_ALLOWED_BET_FIELDS = {
    "doc_id",
    "meeting_date",
    "pm_slack_id",
    "status",
    "metric_name",
    "metric_query",
    "predicted_lift",
    "review_date",
    "github_issue_url",
    "calendar_event_id",
}

_DATE_FIELDS = {"meeting_date", "review_date"}


class Bet(BaseModel):
    """Row of the ``bets`` table.

    SPEC-GAP: the issue spec says ``due_bets -> list[Bet]`` but no Bet model
    exists in models.py (out of this issue's file scope), so it is defined here
    mirroring the bets table from docs/02_DATA_MODEL.md.
    """

    id: UUID
    created_at: datetime
    meeting_date: date
    doc_id: str
    pm_slack_id: str
    status: str
    metric_name: str | None = None
    metric_query: str | None = None
    predicted_lift: float | None = None
    review_date: date | None = None
    github_issue_url: str | None = None
    calendar_event_id: str | None = None
    flags: list[DataFlag] = Field(default_factory=list)


def _as_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _as_date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _flag_to_json(flag: DataFlag) -> str:
    """Serialize a DataFlag for the bets.flags JSONB column."""
    data = asdict(flag)
    data["created_at"] = flag.created_at.isoformat()
    return json.dumps(data)


def _flag_from_json(data: dict[str, object]) -> DataFlag:
    return DataFlag(
        source=Source(str(data["source"])),
        severity=Severity(str(data["severity"])),
        message=str(data["message"]),
        created_at=datetime.fromisoformat(str(data["created_at"])),
    )


def insert_nodes(conn: Connection, nodes: list[GraphNode]) -> None:
    """Insert graph nodes. Referential integrity: each bet_id must exist in bets."""
    if not nodes:
        return
    with conn.cursor() as cur:
        for bet_id in {_as_uuid(n.bet_id) for n in nodes}:
            cur.execute("SELECT 1 FROM bets WHERE id = %s", (bet_id,))
            if cur.fetchone() is None:
                raise PipelineError(
                    "store.insert_nodes", ValueError(f"bet {bet_id} does not exist")
                )
        for n in nodes:
            cur.execute(
                "INSERT INTO graph_nodes (id, bet_id, type, content, confidence, source,"
                " status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    n.id,
                    _as_uuid(n.bet_id),
                    n.type.value,
                    n.content,
                    n.confidence,
                    n.source,
                    n.status.value,
                    n.created_at,
                ),
            )


def insert_edges(conn: Connection, edges: list[GraphEdge]) -> None:
    """Insert graph edges. Referential integrity: both endpoints must be existing nodes."""
    if not edges:
        return
    with conn.cursor() as cur:
        for e in edges:
            endpoints = [_as_uuid(e.from_node), _as_uuid(e.to_node)]
            cur.execute(
                "SELECT id FROM graph_nodes WHERE id = ANY(%s)",
                (endpoints,),
            )
            if len(cur.fetchall()) != 2:
                raise PipelineError(
                    "store.insert_edges",
                    ValueError(f"edge {e.from_node} -> {e.to_node} references a missing node"),
                )
            cur.execute(
                "INSERT INTO graph_edges (id, bet_id, from_node, to_node, edge_type,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    e.id,
                    _as_uuid(e.bet_id),
                    endpoints[0],
                    endpoints[1],
                    e.edge_type.value,
                    e.created_at,
                ),
            )


def create_bet(
    conn: Connection,
    bet_id: UUID | str,
    doc_id: str,
    meeting_date: date | str,
    pm_slack_id: str,
) -> None:
    """Create the bets row (status defaults to EXTRACTING)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bets (id, meeting_date, doc_id, pm_slack_id) VALUES (%s, %s, %s, %s)",
            (_as_uuid(bet_id), _as_date(meeting_date), doc_id, pm_slack_id),
        )


def update_bet_status(conn: Connection, bet_id: UUID | str, status: str) -> None:
    with conn.cursor() as cur:
        if status not in _BET_STATUSES:
            raise PipelineError(
                "store.update_bet_status", ValueError(f"invalid bet status {status!r}")
            )
        cur.execute(
            "UPDATE bets SET status = %s WHERE id = %s",
            (status, _as_uuid(bet_id)),
        )
        if cur.rowcount == 0:
            raise PipelineError("store.update_bet_status", ValueError(f"bet {bet_id} not found"))


def update_bet_fields(conn: Connection, bet_id: UUID | str, **fields: object) -> None:
    """Update whitelisted bet columns; unknown fields are refused, empty update is a no-op."""
    if not fields:
        return
    unknown = set(fields) - _ALLOWED_BET_FIELDS
    if unknown:
        raise PipelineError(
            "store.update_bet_fields",
            ValueError(f"unknown bet fields: {sorted(unknown)}"),
        )
    values = [
        _as_date(v) if isinstance(v, str) and k in _DATE_FIELDS else v for k, v in fields.items()
    ]
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder()) for k in fields
    )
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("UPDATE bets SET {} WHERE id = %s").format(assignments),
            (*values, _as_uuid(bet_id)),
        )
        if cur.rowcount == 0:
            raise PipelineError("store.update_bet_fields", ValueError(f"bet {bet_id} not found"))


def append_flag(conn: Connection, bet_id: UUID | str, flag: DataFlag) -> None:
    """Append a DataFlag to bets.flags JSONB (mirrors the state flag reducer)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE bets SET flags = flags || %s::jsonb WHERE id = %s",
            (_flag_to_json(flag), _as_uuid(bet_id)),
        )
        if cur.rowcount == 0:
            raise PipelineError("store.append_flag", ValueError(f"bet {bet_id} not found"))


def get_bet_graph(conn: Connection, bet_id: UUID | str) -> tuple[list[GraphNode], list[GraphEdge]]:
    """All nodes and edges for one bet."""
    bid = _as_uuid(bet_id)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, bet_id, type, content, confidence, source, status, created_at"
            " FROM graph_nodes WHERE bet_id = %s ORDER BY created_at, id",
            (bid,),
        )
        nodes = [
            GraphNode(
                id=r[0],
                bet_id=r[1],
                type=NodeType(r[2]),
                content=r[3],
                confidence=r[4],
                source=r[5],
                status=NodeStatus(r[6]),
                created_at=r[7],
            )
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT id, bet_id, from_node, to_node, edge_type, created_at"
            " FROM graph_edges WHERE bet_id = %s ORDER BY created_at, id",
            (bid,),
        )
        edges = [
            GraphEdge(
                id=r[0],
                bet_id=r[1],
                from_node=r[2],
                to_node=r[3],
                edge_type=EdgeType(r[4]),
                created_at=r[5],
            )
            for r in cur.fetchall()
        ]
    return nodes, edges


def write_decision(
    conn: Connection, bet_id: UUID | str, chosen_option_id: UUID | str, content: str
) -> UUID:
    """Write the human-attested DECISION node + leads_to edge in one transaction.

    Enforces ADR 0002: refuses if the bet already has a leads_to edge, or if the
    chosen OPTION node does not exist for this bet.
    """
    bid = _as_uuid(bet_id)
    option_id = _as_uuid(chosen_option_id)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM graph_edges WHERE bet_id = %s AND edge_type = %s",
            (bid, EdgeType.LEADS_TO.value),
        )
        if cur.fetchone() is not None:
            raise PipelineError(
                "store.write_decision",
                ValueError(f"bet {bet_id} already has a leads_to edge (decision written)"),
            )
        cur.execute(
            "SELECT 1 FROM graph_nodes WHERE id = %s AND bet_id = %s AND type = %s",
            (option_id, bid, NodeType.OPTION.value),
        )
        if cur.fetchone() is None:
            raise PipelineError(
                "store.write_decision",
                ValueError(f"chosen option {chosen_option_id} not found for bet {bet_id}"),
            )
        decision = GraphNode(bet_id=bid, type=NodeType.DECISION, content=content, source="system")
        cur.execute(
            "INSERT INTO graph_nodes (id, bet_id, type, content, confidence, source,"
            " status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                decision.id,
                bid,
                decision.type.value,
                decision.content,
                decision.confidence,
                decision.source,
                decision.status.value,
                decision.created_at,
            ),
        )
        edge = GraphEdge(
            bet_id=bid,
            from_node=option_id,
            to_node=decision.id,
            edge_type=EdgeType.LEADS_TO,
        )
        cur.execute(
            "INSERT INTO graph_edges (id, bet_id, from_node, to_node, edge_type,"
            " created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                edge.id,
                bid,
                edge.from_node,
                edge.to_node,
                edge.edge_type.value,
                edge.created_at,
            ),
        )
    return decision.id


def write_outcome(
    conn: Connection,
    bet_id: UUID | str,
    hypothesis_id: UUID | str,
    actual: float,
    predicted: float,
    within: bool,
    decision_node: str,
) -> None:
    """Write the outcome DECISION + supports/contradicts edge + outcomes row, one txn.

    Also sets the hypothesis node and the bet to RESOLVED. Idempotent: if an
    outcomes row already exists for the bet, this is a no-op (docs/02 invariant).
    ``within`` and ``delta`` are computed deterministically by the caller/store,
    never by an LLM (ADR 0006).
    """
    bid = _as_uuid(bet_id)
    hyp_id = _as_uuid(hypothesis_id)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM outcomes WHERE bet_id = %s", (bid,))
        if cur.fetchone() is not None:
            return
        cur.execute("SELECT 1 FROM graph_nodes WHERE id = %s AND bet_id = %s", (hyp_id, bid))
        if cur.fetchone() is None:
            raise PipelineError(
                "store.write_outcome",
                ValueError(f"hypothesis {hypothesis_id} not found for bet {bet_id}"),
            )
        node = GraphNode(bet_id=bid, type=NodeType.DECISION, content=decision_node, source="system")
        cur.execute(
            "INSERT INTO graph_nodes (id, bet_id, type, content, confidence, source,"
            " status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                node.id,
                bid,
                node.type.value,
                node.content,
                node.confidence,
                node.source,
                node.status.value,
                node.created_at,
            ),
        )
        edge = GraphEdge(
            bet_id=bid,
            from_node=node.id,
            to_node=hyp_id,
            edge_type=EdgeType.SUPPORTS if within else EdgeType.CONTRADICTS,
        )
        cur.execute(
            "INSERT INTO graph_edges (id, bet_id, from_node, to_node, edge_type,"
            " created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                edge.id,
                bid,
                edge.from_node,
                edge.to_node,
                edge.edge_type.value,
                edge.created_at,
            ),
        )
        cur.execute(
            "INSERT INTO outcomes (bet_id, hypothesis_node_id, decision_node_id,"
            " actual_lift, predicted_lift, delta, within_tolerance)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (bid, hyp_id, node.id, actual, predicted, actual - predicted, within),
        )
        cur.execute(
            "UPDATE graph_nodes SET status = %s WHERE id = %s",
            (NodeStatus.RESOLVED.value, hyp_id),
        )
        cur.execute("UPDATE bets SET status = %s WHERE id = %s", ("RESOLVED", bid))


def due_bets(conn: Connection, demo_mode: bool) -> list[Bet]:
    """Bets awaiting review: status CONFIRMED/SCHEDULED and review_date <= today.

    ``demo_mode`` ignores the review date so stale seeds are always due.
    """
    query = (
        "SELECT id, created_at, meeting_date, doc_id, pm_slack_id, status, metric_name,"
        " metric_query, predicted_lift, review_date, github_issue_url, calendar_event_id,"
        " flags FROM bets WHERE status IN ('CONFIRMED', 'SCHEDULED')"
    )
    params: tuple[object, ...] = ()
    if not demo_mode:
        query += " AND review_date <= CURRENT_DATE"
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [
        Bet(
            id=r[0],
            created_at=r[1],
            meeting_date=r[2],
            doc_id=r[3],
            pm_slack_id=r[4],
            status=r[5],
            metric_name=r[6],
            metric_query=r[7],
            predicted_lift=r[8],
            review_date=r[9],
            github_issue_url=r[10],
            calendar_event_id=r[11],
            flags=[_flag_from_json(f) for f in (r[12] or [])],
        )
        for r in rows
    ]

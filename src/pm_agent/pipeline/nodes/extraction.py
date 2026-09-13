"""Phase 1 LLM extraction nodes (docs/03 §extract_facts, §derive_reasoning).

Thin functions registered on the Phase 1 graph via add_node_checked (M3.5);
tests call them directly. LLM calls go ONLY through pm_agent.llm.call_llm
(ADR 0004). Edge construction and the bets row update are deterministic code
in ONE transaction (AGENTS.md invariant 5). These nodes never write a DECISION
node — only ``proposed_option_id`` (ADR 0002).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from pm_agent.config import get_settings
from pm_agent.db.connection import get_conn
from pm_agent.errors import EmptyExtractionError
from pm_agent.graph import store
from pm_agent.llm import call_llm
from pm_agent.models import (
    DataFlag,
    DerivedReasoning,
    EdgeType,
    ExtractedFacts,
    GraphEdge,
    GraphNode,
    NodeType,
    Severity,
    Source,
)
from pm_agent.pipeline.prompts import DERIVE_REASONING_PROMPT, EXTRACT_FACTS_PROMPT


def extract_facts(state: dict[str, Any]) -> dict[str, Any]:
    """LLM extraction of FACT nodes (docs/03 §extract_facts).

    Guardrail: 0 facts -> ``extract_facts_empty`` flag persisted to
    bets.flags, then ``EmptyExtractionError`` (fail visible, not silent).
    """
    settings = get_settings()
    user = f"Meeting date: {state['meeting_date']}\n\nTRANSCRIPT:\n{state['raw_transcript']}"
    payload = call_llm(settings, EXTRACT_FACTS_PROMPT, user, ExtractedFacts)
    if not payload.facts:
        flag = DataFlag(Source.LLM, Severity.CRITICAL, "extract_facts_empty")
        with get_conn() as conn:
            store.append_flag(conn, state["bet_id"], flag)
        raise EmptyExtractionError("LLM extraction returned 0 facts")
    bet_id = UUID(str(state["bet_id"]))
    nodes = [
        GraphNode(
            bet_id=bet_id,
            type=NodeType.FACT,
            content=item.content,
            source=f"meeting:{state['meeting_date']}:speaker={item.speaker}",
        )
        for item in payload.facts
    ]
    with get_conn() as conn:
        store.insert_nodes(conn, nodes)
    return {"facts": nodes}


def derive_reasoning(state: dict[str, Any]) -> dict[str, Any]:
    """Derive assumptions/hypothesis/options from facts (docs/03 §derive_reasoning).

    Exactly ONE hypothesis is kept (flag ``multiple_hypotheses_collapsed``).
    Nodes, edges and the bets row are written in ONE transaction.
    """
    settings = get_settings()
    facts = state["facts"]
    facts_block = "\n".join(f"[{i}] {n.content}" for i, n in enumerate(facts))
    user = (
        f"Meeting date: {state['meeting_date']}\n\n"
        f"FACTS:\n{facts_block}\n\n"
        f"TRANSCRIPT:\n{state['raw_transcript']}"
    )
    payload = call_llm(settings, DERIVE_REASONING_PROMPT, user, DerivedReasoning)

    flags: list[DataFlag] = []
    hypotheses = list(payload.hypotheses)
    if len(hypotheses) > 1:
        flags.append(DataFlag(Source.LLM, Severity.WARNING, "multiple_hypotheses_collapsed"))
        hypotheses = hypotheses[:1]
    if not hypotheses:
        # SPEC-GAP: docs/03 is silent on 0 hypotheses; failing hard because
        # there would be nothing to confirm.
        raise EmptyExtractionError("LLM extraction returned 0 hypotheses")
    hypothesis = hypotheses[0]

    bet_id = UUID(str(state["bet_id"]))
    source = f"meeting:{state['meeting_date']}:llm"

    assumption_nodes = [
        GraphNode(bet_id=bet_id, type=NodeType.ASSUMPTION, content=a.content, source=source)
        for a in payload.assumptions
    ]
    hypothesis_node = GraphNode(
        bet_id=bet_id,
        type=NodeType.HYPOTHESIS,
        content=hypothesis.content,
        confidence=hypothesis.confidence,
        source=source,
    )
    option_nodes = [
        GraphNode(bet_id=bet_id, type=NodeType.OPTION, content=o.content, source=source)
        for o in payload.options
    ]

    edges: list[GraphEdge] = []
    for node, item in zip(assumption_nodes, payload.assumptions):
        for idx in item.derived_from_fact_ids:
            edges.append(
                GraphEdge(
                    bet_id=bet_id,
                    from_node=node.id,
                    to_node=facts[idx].id,
                    edge_type=EdgeType.DERIVED_FROM,
                )
            )
    for idx in hypothesis.derived_from_fact_ids:
        edges.append(
            GraphEdge(
                bet_id=bet_id,
                from_node=hypothesis_node.id,
                to_node=facts[idx].id,
                edge_type=EdgeType.DERIVED_FROM,
            )
        )
    for idx in hypothesis.depends_on_assumption_idxs:
        edges.append(
            GraphEdge(
                bet_id=bet_id,
                from_node=hypothesis_node.id,
                to_node=assumption_nodes[idx].id,
                edge_type=EdgeType.DEPENDS_ON,
            )
        )
    for node, item in zip(option_nodes, payload.options):
        for idx in item.derived_from_fact_ids:
            edges.append(
                GraphEdge(
                    bet_id=bet_id,
                    from_node=node.id,
                    to_node=facts[idx].id,
                    edge_type=EdgeType.DERIVED_FROM,
                )
            )

    review_date = (
        date.fromisoformat(state["meeting_date"]) + timedelta(days=hypothesis.measurement_days)
    ).isoformat()
    proposed = (
        str(option_nodes[payload.proposed_option_index].id)
        if 0 <= payload.proposed_option_index < len(option_nodes)
        else None
    )

    with get_conn() as conn:
        store.insert_nodes(conn, [*assumption_nodes, hypothesis_node, *option_nodes])
        store.insert_edges(conn, edges)
        store.update_bet_fields(
            conn,
            bet_id,
            metric_name=hypothesis.metric_name,
            metric_query=hypothesis.metric_query,
            predicted_lift=hypothesis.predicted_lift,
            review_date=review_date,
        )

    return {
        "assumptions": assumption_nodes,
        "hypotheses": [hypothesis_node],
        "options": option_nodes,
        "proposed_option_id": proposed,
        "metric_name": hypothesis.metric_name,
        "metric_query": hypothesis.metric_query,
        "predicted_lift": hypothesis.predicted_lift,
        "review_date": review_date,
        "flags": flags,
    }

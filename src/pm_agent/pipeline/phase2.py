"""Phase 2 graph factory + run_due_reviews (docs/03 §Phase 2, ADR 0003/0006).

Graphs are built by factory with the checkpointer injected — NO graph
construction at import time anywhere.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from pm_agent.config import get_settings
from pm_agent.db.connection import get_conn
from pm_agent.graph import store
from pm_agent.nodeutil import add_node_checked
from pm_agent.pipeline.nodes.phase2 import (
    compare_outcome,
    fetch_metric,
    load_bet,
    report_outcome,
    update_calibration,
)
from pm_agent.state import BetState, create_initial_state


def build_phase2_graph(checkpointer: Any = None):
    """Compile the Phase 2 graph with the injected checkpointer (ADR 0003)."""
    graph = StateGraph(BetState)
    add_node_checked(graph, "load_bet", load_bet)
    add_node_checked(graph, "fetch_metric", fetch_metric)
    add_node_checked(graph, "compare_outcome", compare_outcome)
    add_node_checked(graph, "update_calibration", update_calibration)
    add_node_checked(graph, "report_outcome", report_outcome)
    graph.add_edge(START, "load_bet")
    graph.add_edge("load_bet", "fetch_metric")
    graph.add_edge("fetch_metric", "compare_outcome")
    graph.add_edge("compare_outcome", "update_calibration")
    graph.add_edge("update_calibration", "report_outcome")
    graph.add_edge("report_outcome", END)
    return graph.compile(checkpointer=checkpointer)


def run_due_reviews() -> dict[str, int]:
    """Find due bets, run Phase 2 per bet. Returns {checked, resolved, flagged}.

    Used by /reviews/check and the CLI. Phase 2 has no interrupts, so an
    in-memory checkpointer per bet suffices (thread_id = bet_id:review, ADR 0003).
    """
    from langgraph.checkpoint.memory import MemorySaver

    settings = get_settings()
    with get_conn() as conn:
        bets = store.due_bets(conn, demo_mode=settings.demo_mode)
    checked = resolved = flagged = 0
    for bet in bets:
        thread_id = f"{bet.id}:review"
        state = create_initial_state(str(bet.id), bet.doc_id, bet.meeting_date.isoformat())
        graph = build_phase2_graph(MemorySaver())
        try:
            final = graph.invoke(state, {"configurable": {"thread_id": thread_id}})
        except Exception:
            flagged += 1
            continue
        checked += 1
        if final.get("delta") is not None:
            resolved += 1
        if final.get("flags"):
            flagged += 1
    return {"checked": checked, "resolved": resolved, "flagged": flagged}

"""Phase 1 graph factory (docs/03 §Phase 1, ADR 0003).

Graphs are built by factory with the checkpointer injected — NO graph
construction at import time anywhere.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from pm_agent.nodeutil import add_node_checked
from pm_agent.pipeline.nodes.decision import confirm_decision, write_decision
from pm_agent.pipeline.nodes.extraction import derive_reasoning, extract_facts
from pm_agent.pipeline.nodes.intake import docs_intake
from pm_agent.pipeline.nodes.phase1_effects import link_github, schedule_review
from pm_agent.state import BetState


def build_phase1_graph(checkpointer: Any):
    """Compile the Phase 1 graph with the injected checkpointer (ADR 0003)."""
    graph = StateGraph(BetState)
    add_node_checked(graph, "docs_intake", docs_intake)
    add_node_checked(graph, "extract_facts", extract_facts)
    add_node_checked(graph, "derive_reasoning", derive_reasoning)
    add_node_checked(graph, "confirm_decision", confirm_decision)
    add_node_checked(graph, "write_decision", write_decision)
    add_node_checked(graph, "link_github", link_github)
    add_node_checked(graph, "schedule_review", schedule_review)
    graph.add_edge(START, "docs_intake")
    graph.add_edge("docs_intake", "extract_facts")
    graph.add_edge("extract_facts", "derive_reasoning")
    graph.add_edge("derive_reasoning", "confirm_decision")
    graph.add_edge("confirm_decision", "write_decision")
    graph.add_edge("write_decision", "link_github")
    graph.add_edge("link_github", "schedule_review")
    graph.add_edge("schedule_review", END)
    return graph.compile(checkpointer=checkpointer)

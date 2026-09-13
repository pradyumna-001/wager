"""confirm_decision (interrupt) + write_decision nodes (docs/03, ADR 0002).

DECISION nodes are human-attested: confirm_decision posts the Slack message and
pauses at ``interrupt()``; only after the PM resumes does the DECISION node get
written (invariant 2).
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from pm_agent.config import get_settings
from pm_agent.db.connection import get_conn
from pm_agent.errors import IntegrationError
from pm_agent.graph import store
from pm_agent.integrations.slack import post_confirm
from pm_agent.models import DataFlag, Severity, Source


def confirm_decision(state: dict[str, Any]) -> dict[str, Any]:
    """Post the Slack confirmation, then pause at the interrupt (ADR 0002).

    On Slack failure: flag ``slack_confirm_failed`` (state + bets.flags),
    status PENDING_CONFIRMATION, raise IntegrationError (API maps to 502).
    On resume: validates the option index and sets ``chosen_option_id``.

    # SPEC-QUESTION: docs/03 says write_decision reads the Command(resume=...)
    # payload, but the resume value is only available inside the interrupting
    # node in LangGraph; the index is validated here and chosen_option_id is
    # passed via state so write_decision can consume it.
    """
    settings = get_settings()
    options = [n.content for n in state["options"]]
    proposed_index = 0
    for i, node in enumerate(state["options"]):
        if str(node.id) == state["proposed_option_id"]:
            proposed_index = i
            break
    result = post_confirm(
        settings, state["bet_id"], options, proposed_index, state["hypotheses"][0].content
    )
    with get_conn() as conn:
        if result.error is not None:
            flag = DataFlag(
                Source.SLACK, Severity.CRITICAL, f"slack_confirm_failed: {result.error.message}"
            )
            store.update_bet_status(conn, state["bet_id"], "PENDING_CONFIRMATION")
            store.append_flag(conn, state["bet_id"], flag)
        else:
            store.update_bet_status(conn, state["bet_id"], "PENDING_CONFIRMATION")
    if result.error is not None:
        raise IntegrationError(f"slack confirm failed: {result.error.message}")

    chosen = interrupt(
        {"bet_id": state["bet_id"], "options": options, "proposed_index": proposed_index}
    )
    index = chosen["chosen_option_index"]
    if not 0 <= index < len(state["options"]):
        raise IntegrationError(f"invalid chosen_option_index {index}")
    return {"chosen_option_id": str(state["options"][index].id)}


def write_decision(state: dict[str, Any]) -> dict[str, Any]:
    """Write the human-attested DECISION node + leads_to edge (ADR 0002).

    # SPEC-QUESTION: docs/03 says the DECISION node source is
    # "slack:confirmation", but merged store.write_decision hardcodes
    # source="system" (out of this issue's scope); the store behavior is used.
    """
    chosen_id = state["chosen_option_id"]
    if chosen_id is None:
        raise IntegrationError("no chosen_option_id (resume payload missing)")
    option = next((n for n in state["options"] if str(n.id) == chosen_id), None)
    if option is None:
        raise IntegrationError(f"chosen option {chosen_id} not found")
    with get_conn() as conn:
        decision_id = store.write_decision(
            conn, state["bet_id"], chosen_id, f"Chose: {option.content}"
        )
        store.update_bet_status(conn, state["bet_id"], "CONFIRMED")
    return {"decision_node_id": str(decision_id)}

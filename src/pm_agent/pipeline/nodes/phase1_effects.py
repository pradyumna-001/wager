"""link_github + schedule_review nodes (docs/03 §link_github, §schedule_review).

Side-effect nodes: failure appends a flag to state AND persists it to
bets.flags via store.append_flag, then continues (fail-visible, never silent).
"""

from __future__ import annotations

from typing import Any

from pm_agent.config import get_settings
from pm_agent.db.connection import get_conn
from pm_agent.graph import store
from pm_agent.integrations.gcal import create_review_event
from pm_agent.integrations.github import create_or_comment_issue
from pm_agent.models import DataFlag, Severity, Source


def link_github(state: dict[str, Any]) -> dict[str, Any]:
    """Create/comment the GitHub issue; failure -> flag (persisted) + continue."""
    settings = get_settings()
    hypothesis = state["hypotheses"][0].content
    title = f"Bet: {hypothesis[:80]}"
    body = (
        f"Hypothesis: {hypothesis}\n"
        f"Metric: {state['metric_name']}\n"
        f"Predicted lift: +{state['predicted_lift']}pp\n"
        f"Review date: {state['review_date']}"
    )
    result = create_or_comment_issue(settings, state["bet_id"], title, body)
    if result.error is not None:
        flag = DataFlag(
            Source.GITHUB, Severity.CRITICAL, f"github_write_failed: {result.error.message}"
        )
        with get_conn() as conn:
            store.append_flag(conn, state["bet_id"], flag)
        return {"flags": [flag]}
    with get_conn() as conn:
        store.update_bet_fields(conn, state["bet_id"], github_issue_url=result.value)
    return {"github_issue_url": result.value}


def schedule_review(state: dict[str, Any]) -> dict[str, Any]:
    """Create the review calendar event; failure -> flag (persisted) + continue."""
    settings = get_settings()
    result = create_review_event(
        settings,
        state["review_date"],
        f"Review bet: {state['metric_name']}",
        f"{state['metric_query']}\n\nbet_id: {state['bet_id']}",
    )
    if result.error is not None:
        flag = DataFlag(
            Source.GCAL, Severity.CRITICAL, f"calendar_write_failed: {result.error.message}"
        )
        with get_conn() as conn:
            store.append_flag(conn, state["bet_id"], flag)
        return {"flags": [flag]}
    with get_conn() as conn:
        store.update_bet_fields(
            conn, state["bet_id"], calendar_event_id=result.value, status="SCHEDULED"
        )
    return {"calendar_event_id": result.value}

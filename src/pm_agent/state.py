"""LangGraph shared state for one bet (one Phase 1 run = one bet, docs/02)."""

from operator import add
from typing import Annotated, TypedDict

from pm_agent.models import DataFlag, GraphNode


class BetState(TypedDict):
    bet_id: str
    doc_id: str  # google doc id for this meeting
    meeting_date: str  # ISO date, used in source tags

    raw_transcript: str

    facts: list[GraphNode]
    assumptions: list[GraphNode]
    hypotheses: list[GraphNode]
    options: list[GraphNode]

    proposed_option_id: str | None  # LLM guess, pre-confirmation
    chosen_option_id: str | None  # set ONLY by the Slack confirm resume
    decision_node_id: str | None

    metric_name: str | None  # e.g. "signup_completion_rate"
    metric_query: str | None  # HogQL SELECT
    predicted_lift: float | None  # percentage points, e.g. 15.0
    review_date: str | None  # ISO date

    github_issue_url: str | None
    calendar_event_id: str | None

    actual_lift: float | None
    delta: float | None  # actual - predicted
    calibration_mean_abs_delta: float | None
    calibration_hit_rate: float | None

    flags: Annotated[list[DataFlag], add]  # fail-visible; nodes ONLY append


def create_initial_state(bet_id: str, doc_id: str, meeting_date: str) -> BetState:
    """Initialize EVERY key. Entrypoints and tests must use this, never literals."""
    return BetState(
        bet_id=bet_id,
        doc_id=doc_id,
        meeting_date=meeting_date,
        raw_transcript="",
        facts=[],
        assumptions=[],
        hypotheses=[],
        options=[],
        proposed_option_id=None,
        chosen_option_id=None,
        decision_node_id=None,
        metric_name=None,
        metric_query=None,
        predicted_lift=None,
        review_date=None,
        github_issue_url=None,
        calendar_event_id=None,
        actual_lift=None,
        delta=None,
        calibration_mean_abs_delta=None,
        calibration_hit_rate=None,
        flags=[],
    )

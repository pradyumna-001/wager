"""Phase 2 review-graph nodes (docs/03 §Phase 2).

All deterministic (ADR 0006 — grading/calibration are plain code, no LLM).
PostHog failure leaves the bet un-RESOLVED and continues to the report
(fail-visible, never silent). Nodes return ONLY BetState keys.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pm_agent.config import get_settings
from pm_agent.db.connection import get_conn
from pm_agent.graph import store
from pm_agent.graph.calibration import compute_calibration
from pm_agent.integrations.github import comment_on_issue
from pm_agent.integrations.posthog import run_hogql
from pm_agent.integrations.slack import post_report
from pm_agent.models import DataFlag, EdgeType, NodeType, Severity, Source
from pm_agent.report.render import build_report_text


def load_bet(state: dict[str, Any]) -> dict[str, Any]:
    """Reload everything from Postgres — Phase 1 state is never reused."""
    bet_id = state["bet_id"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT meeting_date, doc_id, metric_name, metric_query, predicted_lift,"
                " review_date FROM bets WHERE id = %s",
                (bet_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise ValueError(f"bet {bet_id} not found")
        meeting_date, doc_id, metric_name, metric_query, predicted_lift, review_date = row
        nodes, _edges = store.get_bet_graph(conn, bet_id)
    hypotheses = [n for n in nodes if n.type == NodeType.HYPOTHESIS]
    if not hypotheses:
        raise ValueError(f"bet {bet_id} has no HYPOTHESIS node")
    return {
        "meeting_date": meeting_date.isoformat(),
        "doc_id": doc_id,
        "metric_name": metric_name,
        "metric_query": metric_query,
        "predicted_lift": predicted_lift,
        "review_date": review_date.isoformat() if review_date else None,
        "facts": [n for n in nodes if n.type == NodeType.FACT],
        "assumptions": [n for n in nodes if n.type == NodeType.ASSUMPTION],
        "hypotheses": hypotheses,
        "options": [n for n in nodes if n.type == NodeType.OPTION],
    }


def fetch_metric(state: dict[str, Any]) -> dict[str, Any]:
    """PostHog HogQL query -> actual_lift; failure -> flag (persisted) + continue."""
    settings = get_settings()
    result = run_hogql(settings, state["metric_query"])
    if result.error is not None:
        with get_conn() as conn:
            store.append_flag(conn, state["bet_id"], result.error)
        return {"actual_lift": None, "flags": [result.error]}
    return {"actual_lift": result.value}


def compare_outcome(state: dict[str, Any]) -> dict[str, Any]:
    """Deterministic grading (ADR 0006): abs(delta) <= tolerance is a HIT.

    Skip grading when actual_lift is None (PostHog failed): bets stays
    SCHEDULED so the next check retries it.
    """
    actual = state["actual_lift"]
    if actual is None:
        return {}
    predicted = state["predicted_lift"]
    delta = actual - predicted
    within = abs(delta) <= get_settings().bet_tolerance_pp
    hypothesis = state["hypotheses"][0]
    content = f"Outcome: actual {actual:+}pp vs predicted {predicted:+}pp (delta {delta:+}pp)"
    # SPEC-QUESTION: docs/03 says the outcome DECISION source is
    # "posthog:<query_id_or_query_hash>", but merged store.write_outcome
    # hardcodes source="system" (out of this issue's scope).
    with get_conn() as conn:
        store.write_outcome(
            conn, state["bet_id"], hypothesis.id, actual, predicted, within, content
        )
    return {"delta": delta}


def update_calibration(state: dict[str, Any]) -> dict[str, Any]:
    """Recompute calibration AFTER this bet's outcomes row (deterministic)."""
    with get_conn() as conn:
        calibration = compute_calibration(conn)
    return {
        "calibration_mean_abs_delta": calibration.mean_abs_delta,
        "calibration_hit_rate": calibration.hit_rate,
    }


def _weakest_assumption(state: dict[str, Any]) -> str | None:
    """First ASSUMPTION reachable via the hypothesis's depends_on edges,
    deterministic: lowest created_at (docs/03 §report_outcome)."""
    hypothesis = state["hypotheses"][0]
    bid = state["bet_id"]
    with get_conn() as conn:
        nodes, edges = store.get_bet_graph(conn, bid)
    assumption_by_id = {str(n.id): n for n in nodes if n.type == NodeType.ASSUMPTION}
    targets = [
        e.to_node
        for e in edges
        if e.edge_type == EdgeType.DEPENDS_ON and str(e.from_node) == str(hypothesis.id)
    ]
    candidates = sorted(
        (assumption_by_id[str(t)] for t in targets if str(t) in assumption_by_id),
        key=lambda a: a.created_at,
    )
    if candidates:
        return candidates[0].content
    assumptions = [n for n in nodes if n.type == NodeType.ASSUMPTION]
    return assumptions[0].content if assumptions else None


class _BetView:
    """Duck-typed bet view for build_report_text (docs/03 signature)."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.hypothesis = state["hypotheses"][0].content
        self.metric_name = state["metric_name"]
        self.predicted_lift = state["predicted_lift"]
        window = date.fromisoformat(state["review_date"]) - date.fromisoformat(
            state["meeting_date"]
        )
        self.window = f"{window.days} days"


class _OutcomeView:
    """Duck-typed outcome view for build_report_text (docs/03 signature)."""

    def __init__(self, state: dict[str, Any], tolerance: float) -> None:
        self.actual_lift = state["actual_lift"]
        self.delta = state.get("delta")
        self.supports = (
            state["actual_lift"] is not None
            and abs(state["actual_lift"] - state["predicted_lift"]) <= tolerance
        )
        self.tolerance = tolerance
        self.assumption_content = _weakest_assumption(state)


class _CalibrationView:
    """Duck-typed calibration view (n_resolved is not a BetState key — fresh read)."""

    def __init__(self, state: dict[str, Any]) -> None:
        with get_conn() as conn:
            calibration = compute_calibration(conn)
        self.mean_abs_delta = calibration.mean_abs_delta
        self.n_resolved = calibration.n_resolved
        self.hit_rate = calibration.hit_rate


def report_outcome(state: dict[str, Any]) -> dict[str, Any]:
    """Build the report text (deterministic, ADR 0001) and send Slack + GitHub."""
    settings = get_settings()
    bet = _BetView(state)
    outcome = _OutcomeView(state, settings.bet_tolerance_pp)
    calibration = _CalibrationView(state)
    text = build_report_text(bet, outcome, calibration, state.get("flags", []))
    new_flags: list[DataFlag] = []
    report = post_report(settings, text)
    if report.error is not None:
        new_flags.append(
            DataFlag(
                Source.SLACK, Severity.CRITICAL, f"slack_report_failed: {report.error.message}"
            )
        )
    if state.get("github_issue_url"):
        comment = comment_on_issue(settings, state["github_issue_url"], text)
        if comment.error is not None:
            new_flags.append(
                DataFlag(
                    Source.GITHUB,
                    Severity.CRITICAL,
                    f"github_comment_failed: {comment.error.message}",
                )
            )
    return {"flags": new_flags}

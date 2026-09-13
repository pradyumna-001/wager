"""API routes (docs/05)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Request
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from pydantic import BaseModel

from pm_agent.api.errors import ApiError, ErrorCode
from pm_agent.config import get_settings
from pm_agent.db.connection import get_conn
from pm_agent.errors import EmptyExtractionError, IntegrationError
from pm_agent.graph import store
from pm_agent.graph.calibration import compute_calibration
from pm_agent.integrations.slack import verify_slack_signature
from pm_agent.models import DataFlag, Severity, Source
from pm_agent.pipeline.phase1 import build_phase1_graph
from pm_agent.pipeline.phase2 import build_phase2_graph
from pm_agent.state import create_initial_state

logger = logging.getLogger("pm_agent.api")

router = APIRouter()


class Phase1RunRequest(BaseModel):
    doc_id: str | None = None


def _saver() -> PostgresSaver:
    return PostgresSaver.from_conn_string(get_settings().database_url)


def _pending_exists(doc_id: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM bets WHERE doc_id = %s AND status = 'PENDING_CONFIRMATION'",
                (doc_id,),
            )
            return cur.fetchone() is not None


def _create_bet(doc_id: str) -> str:
    bet_id = str(uuid.uuid4())
    with get_conn() as conn:
        store.create_bet(
            conn, bet_id, doc_id, date.today().isoformat(), get_settings().pm_slack_user_id
        )
    return bet_id


@router.get("/healthz")
def healthz() -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    return {"ok": True}


def _run_phase1_background(bet_id: str, doc_id: str) -> None:
    try:
        with _saver() as saver:
            saver.setup()
            graph = build_phase1_graph(saver)
            state = create_initial_state(bet_id, doc_id, date.today().isoformat())
            graph.invoke(state, {"configurable": {"thread_id": bet_id}})
    except Exception:
        logger.exception("phase1 background run failed", extra={"bet_id": bet_id})


@router.post("/phase1/run", status_code=202)
def phase1_run(payload: Phase1RunRequest, background: BackgroundTasks) -> dict:
    doc_id = payload.doc_id or get_settings().google_doc_id
    if _pending_exists(doc_id):
        raise ApiError(
            ErrorCode.ALREADY_PENDING,
            "a bet is already pending confirmation for this doc",
            status=409,
        )
    bet_id = _create_bet(doc_id)
    background.add_task(_run_phase1_background, bet_id, doc_id)
    return {"bet_id": bet_id, "status": "EXTRACTING"}


@router.post("/phase1/{bet_id}/run-sync")
def phase1_run_sync(bet_id: str, payload: Phase1RunRequest | None = None) -> dict:
    """Run Phase 1 synchronously up to the interrupt (demo/testing convenience)."""
    doc_id = (payload.doc_id if payload else None) or get_settings().google_doc_id
    try:
        with get_conn() as conn:
            store.create_bet(
                conn, bet_id, doc_id, date.today().isoformat(), get_settings().pm_slack_user_id
            )
        with _saver() as saver:
            saver.setup()
            graph = build_phase1_graph(saver)
            state = create_initial_state(bet_id, doc_id, date.today().isoformat())
            result = graph.invoke(state, {"configurable": {"thread_id": bet_id}})
    except EmptyExtractionError:
        raise ApiError(ErrorCode.EMPTY_EXTRACTION, "extraction produced no facts", status=422)
    except IntegrationError as exc:
        raise ApiError(ErrorCode.GDOCS_FAILED, str(exc), status=502)
    if "__interrupt__" in result:
        value = result["__interrupt__"][0].value
        return {
            "bet_id": bet_id,
            "status": "PENDING_CONFIRMATION",
            "options": value["options"],
            "proposed_index": value["proposed_index"],
        }
    return {"bet_id": bet_id, "status": "CONFIRMED"}


@router.post("/slack/interactions")
async def slack_interactions(request: Request) -> dict:
    """Slack interactivity webhook: verify signature, resume the graph, always 200."""
    body = await request.body()
    settings = get_settings()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(settings, body, timestamp, signature):
        raise ApiError(ErrorCode.BAD_SIGNATURE, "invalid Slack signature", status=401)
    payload = json.loads(body.decode("utf-8"))
    if payload.get("type") != "block_actions" or not payload.get("actions"):
        return {"text": "ignored"}
    action = payload["actions"][0]
    index = int(action["action_id"].split(":")[-1])
    bet_id = action["value"]
    try:
        with _saver() as saver:
            saver.setup()
            graph = build_phase1_graph(saver)
            result = graph.invoke(
                Command(resume={"chosen_option_index": index}),
                {"configurable": {"thread_id": bet_id}},
            )
    except Exception as exc:
        logger.exception("slack resume failed", extra={"bet_id": bet_id})
        with get_conn() as conn:
            store.append_flag(
                conn,
                bet_id,
                DataFlag(Source.SLACK, Severity.CRITICAL, f"slack_resume_failed: {exc}"),
            )
        return {"text": "❌ Could not record the decision — it will be retried."}
    review_date = result.get("review_date") or ""
    return {"text": f"✅ Recorded. Bet tracked — I'll come back on {review_date}."}


@router.post("/reviews/check")
def reviews_check() -> dict:
    """Run Phase 2 for all due bets; returns bet_ids + flags (docs/05 shape)."""
    from langgraph.checkpoint.memory import MemorySaver

    settings = get_settings()
    with get_conn() as conn:
        bets = store.due_bets(conn, demo_mode=settings.demo_mode)
    checked = 0
    resolved: list[str] = []
    flagged: list[dict] = []
    for bet in bets:
        state = create_initial_state(str(bet.id), bet.doc_id, bet.meeting_date.isoformat())
        graph = build_phase2_graph(MemorySaver())
        try:
            final = graph.invoke(state, {"configurable": {"thread_id": f"{bet.id}:review"}})
        except Exception as exc:
            flagged.append({"bet_id": str(bet.id), "flags": [str(exc)]})
            continue
        checked += 1
        if final.get("delta") is not None:
            resolved.append(str(bet.id))
        if final.get("flags"):
            flagged.append({"bet_id": str(bet.id), "flags": [f.message for f in final["flags"]]})
    return {"checked": checked, "resolved": resolved, "flagged": flagged}


@router.get("/bets/{bet_id}")
def get_bet(bet_id: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, metric_name, predicted_lift, review_date"
                " FROM bets WHERE id = %s",
                (bet_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise ApiError(ErrorCode.BET_NOT_FOUND, f"bet {bet_id} not found", status=404)
        nodes, edges = store.get_bet_graph(conn, bet_id)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT actual_lift, delta, within_tolerance FROM outcomes WHERE bet_id = %s",
                (bet_id,),
            )
            outcome_row = cur.fetchone()
    outcome = None
    if outcome_row is not None:
        outcome = {
            "actual_lift": outcome_row[0],
            "delta": outcome_row[1],
            "within_tolerance": outcome_row[2],
        }
    return {
        "bet": {
            "id": str(row[0]),
            "status": row[1],
            "metric_name": row[2],
            "predicted_lift": row[3],
            "review_date": row[4].isoformat() if row[4] else None,
        },
        "nodes": [
            {
                "id": str(n.id),
                "type": str(n.type),
                "content": n.content,
                "confidence": n.confidence,
                "source": n.source,
                "status": str(n.status),
            }
            for n in nodes
        ],
        "edges": [
            {
                "from_node": str(e.from_node),
                "to_node": str(e.to_node),
                "edge_type": str(e.edge_type),
            }
            for e in edges
        ],
        "outcome": outcome,
    }


@router.get("/calibration")
def calibration() -> dict:
    with get_conn() as conn:
        cal = compute_calibration(conn)
    return {
        "mean_abs_delta": cal.mean_abs_delta,
        "hit_rate": cal.hit_rate,
        "n_resolved": cal.n_resolved,
        "tolerance_pp": get_settings().bet_tolerance_pp,
    }

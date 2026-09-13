"""docs_intake node (docs/03 §docs_intake).

Hard-stop node: Phase 1 cannot proceed without input, so a FetchResult error
raises IntegrationError here (the API layer maps it to 502).
"""

from __future__ import annotations

from typing import Any

from pm_agent.config import get_settings
from pm_agent.db.connection import get_conn
from pm_agent.errors import IntegrationError
from pm_agent.graph import store
from pm_agent.integrations.gdocs import fetch_transcript


def docs_intake(state: dict[str, Any]) -> dict[str, Any]:
    """Fetch the transcript; hard-stop on failure, else set raw_transcript."""
    result = fetch_transcript(get_settings(), state["doc_id"])
    if result.error is not None:
        raise IntegrationError(f"docs_intake failed: {result.error.message}")
    with get_conn() as conn:
        store.update_bet_status(conn, state["bet_id"], "EXTRACTING")
    return {"raw_transcript": result.value}

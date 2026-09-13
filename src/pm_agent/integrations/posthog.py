"""PostHog integration (docs/04 §posthog.py): HogQL metric queries + seeding."""

import httpx

from pm_agent.config import Settings
from pm_agent.integrations.base import FetchResult
from pm_agent.models import DataFlag, Severity, Source

_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def _flag(message: str) -> DataFlag:
    return DataFlag(Source.POSTHOG, Severity.WARNING, message)


def _headers(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.posthog_personal_api_key}"}


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT)


def run_hogql(settings: Settings, query: str) -> FetchResult[float]:
    """POST /api/projects/{project_id}/query with a HogQL query.

    Value = first cell of first row parsed as float (the query contract:
    exactly one numeric column = lift in percentage points). Failure mapping:
    httpx timeout -> WARNING ``posthog_timeout``; non-2xx ->
    ``posthog_error: <status>``; empty/malformed -> ``posthog_empty_result``.
    Never raises (ADR 0005).
    """
    url = f"{settings.posthog_host}/api/projects/{settings.posthog_project_id}/query"
    payload = {"query": {"kind": "HogQLQuery", "query": query}}
    try:
        with _client() as client:
            resp = client.post(url, headers=_headers(settings), json=payload)
    except httpx.TimeoutException:
        return FetchResult(None, _flag("posthog_timeout"))
    except Exception as e:
        return FetchResult(None, _flag(f"posthog_error: {e}"))
    if resp.status_code >= 400:
        return FetchResult(None, _flag(f"posthog_error: {resp.status_code}"))
    try:
        return FetchResult(float(resp.json()["results"][0][0]), None)
    except Exception:
        return FetchResult(None, _flag("posthog_empty_result"))


# SPEC-QUESTION: docs/04 shows capture_event -> None, but this issue and
# ADR 0005 mandate a FetchResult; the seed script treats failure as non-fatal.
def capture_event(
    settings: Settings, distinct_id: str, event: str, properties: dict, timestamp: str
) -> FetchResult[None]:
    """POST /capture/ — used ONLY by scripts/seed_posthog.py (failure non-fatal)."""
    url = f"{settings.posthog_host}/capture/"
    payload = {
        "distinct_id": distinct_id,
        "event": event,
        "properties": properties,
        "timestamp": timestamp,
    }
    try:
        with _client() as client:
            resp = client.post(url, headers=_headers(settings), json=payload)
    except httpx.TimeoutException:
        return FetchResult(None, _flag("posthog_timeout"))
    except Exception as e:
        return FetchResult(None, _flag(f"posthog_error: {e}"))
    if resp.status_code >= 400:
        return FetchResult(None, _flag(f"posthog_error: {resp.status_code}"))
    return FetchResult(None, None)

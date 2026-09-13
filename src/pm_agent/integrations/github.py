"""GitHub issue integration (docs/04 §github.py)."""

import httpx

from pm_agent.config import Settings
from pm_agent.integrations.base import FetchResult
from pm_agent.models import DataFlag, Severity, Source

_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def _flag(message: str) -> DataFlag:
    return DataFlag(Source.GITHUB, Severity.CRITICAL, message)


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
    }


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT)


def _issues_url(settings: Settings) -> str:
    return f"https://api.github.com/repos/{settings.github_repo}/issues"


def _issue_marker(bet_id: str) -> str:
    return f"<!-- bet_id:{bet_id} -->"


def create_or_comment_issue(
    settings: Settings, bet_id: str, title: str, body: str
) -> FetchResult[str]:
    """Create an issue for the bet, or comment on the existing one.

    Idempotency: the issue body carries ``<!-- bet_id:{bet_id} -->``. If an
    existing issue already carries the marker, ``body`` is posted as a comment
    on it and its ``html_url`` is returned; otherwise a new issue is created
    and the new ``html_url`` returned. Non-2xx and transport errors ->
    ``github_error`` DataFlag, never raises (ADR 0005).
    """
    marker = _issue_marker(bet_id)
    try:
        with _client() as client:
            resp = client.get(
                f"{_issues_url(settings)}?state=all&per_page=100", headers=_headers(settings)
            )
            if resp.status_code >= 400:
                return FetchResult(None, _flag(f"github_error: {resp.status_code}"))
            existing = next((i for i in resp.json() if marker in (i.get("body") or "")), None)
            if existing is not None:
                comment_url = f"{_issues_url(settings)}/{existing['number']}/comments"
                c_resp = client.post(comment_url, headers=_headers(settings), json={"body": body})
                if c_resp.status_code >= 400:
                    return FetchResult(None, _flag(f"github_error: {c_resp.status_code}"))
                return FetchResult(existing["html_url"], None)
            resp = client.post(
                _issues_url(settings),
                headers=_headers(settings),
                json={"title": title, "body": f"{body}\n\n{marker}"},
            )
            if resp.status_code >= 400:
                return FetchResult(None, _flag(f"github_error: {resp.status_code}"))
            return FetchResult(resp.json()["html_url"], None)
    except Exception as e:
        return FetchResult(None, _flag(f"github_error: {e}"))


def comment_on_issue(settings: Settings, issue_url: str, text: str) -> FetchResult[None]:
    """Comment on an existing issue (used by Phase 2). Never raises (ADR 0005)."""
    try:
        number = issue_url.rstrip("/").split("/")[-1]
        with _client() as client:
            resp = client.post(
                f"{_issues_url(settings)}/{number}/comments",
                headers=_headers(settings),
                json={"body": text},
            )
        if resp.status_code >= 400:
            return FetchResult(None, _flag(f"github_error: {resp.status_code}"))
        return FetchResult(None, None)
    except Exception as e:
        return FetchResult(None, _flag(f"github_error: {e}"))

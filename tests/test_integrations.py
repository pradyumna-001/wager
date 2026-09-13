"""Integration tests (result objects; every failure branch -> exact DataFlag).

Google API mocked at the client boundary (monkeypatched in the gdocs module
namespace); no network, no SA file reads.
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import pm_agent.integrations.gcal as gcal_mod
import pm_agent.integrations.gdocs as gdocs
import pm_agent.integrations.github as github_mod
from pm_agent.config import get_settings
from pm_agent.integrations.base import FetchResult
from pm_agent.models import Severity, Source


def _paragraph(*runs: str) -> dict[str, Any]:
    return {"paragraph": {"elements": [{"textRun": {"content": run}} for run in runs]}}


def _fake_google(
    monkeypatch: pytest.MonkeyPatch,
    doc: dict[str, Any] | None = None,
    execute_error: Exception | None = None,
) -> list[tuple[Any, ...]]:
    """Install fake Credentials + discovery.build; return recorded build calls."""
    build_calls: list[tuple[Any, ...]] = []

    class FakeCredentials:
        @classmethod
        def from_service_account_file(cls, path: str) -> object:
            return object()

    class FakeRequest:
        def execute(self) -> dict[str, Any]:
            if execute_error is not None:
                raise execute_error
            assert doc is not None
            return doc

    class FakeDocuments:
        def get(self, *, documentId: str) -> FakeRequest:
            return FakeRequest()

    class FakeService:
        def documents(self) -> FakeDocuments:
            return FakeDocuments()

    def fake_build(service_name: str, version: str, **kwargs: Any) -> FakeService:
        build_calls.append((service_name, version, kwargs))
        return FakeService()

    monkeypatch.setattr(gdocs, "Credentials", FakeCredentials)
    monkeypatch.setattr(gdocs, "build", fake_build)
    return build_calls


def test_gdocs_fetch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = {
        "body": {
            "content": [
                _paragraph("Hello ", "there"),
                {"table": {"rows": 1}},
                _paragraph("World"),
                {"paragraph": {"elements": [{"columnBreak": {}}]}},
            ]
        }
    }
    build_calls = _fake_google(monkeypatch, doc=doc)

    result = gdocs.fetch_transcript(get_settings(), "doc-123")

    assert isinstance(result, FetchResult)
    assert result.value == "Hello thereWorld"
    assert result.error is None
    assert build_calls[0][:2] == ("docs", "v1")
    assert "credentials" in build_calls[0][2]


def test_gdocs_fetch_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_google(monkeypatch, execute_error=RuntimeError("docs api down"))

    result = gdocs.fetch_transcript(get_settings(), "doc-123")

    assert result.value is None
    assert result.error is not None
    assert result.error.source == Source.GDOCS
    assert result.error.severity == Severity.CRITICAL
    assert result.error.message.startswith("gdocs_fetch_failed")


def test_gdocs_fetch_empty_doc(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_google(monkeypatch, doc={"body": {"content": []}})

    result = gdocs.fetch_transcript(get_settings(), "doc-123")

    assert result.value is None
    assert result.error is not None
    assert result.error.source == Source.GDOCS
    assert result.error.message == "gdocs_empty_doc"


# --- github.py ---


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data: Any = None) -> None:
        self.status_code = status_code
        self._json = json_data

    def json(self) -> Any:
        return self._json


class _FakeClient:
    def __init__(self, handler: Callable[[str, str, Any], _FakeResponse]) -> None:
        self._handler = handler
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self._handler("GET", url, None)

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self._handler("POST", url, kwargs.get("json"))


def _fake_httpx(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[str, str, Any], _FakeResponse]
) -> _FakeClient:
    """Replace github.httpx with a stub namespace returning a shared fake client."""
    client = _FakeClient(handler)
    monkeypatch.setattr(
        github_mod, "httpx", SimpleNamespace(Client=lambda *a, **k: client, Timeout=httpx.Timeout)
    )
    return client


def test_github_create_issue_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, payload: Any) -> _FakeResponse:
        if method == "GET":
            return _FakeResponse(200, [])
        return _FakeResponse(201, {"html_url": "https://github.com/o/r/issues/9"})

    client = _fake_httpx(monkeypatch, handler)
    settings = get_settings()

    result = github_mod.create_or_comment_issue(settings, "bet-1", "Bet title", "Bet body")

    assert result.value == "https://github.com/o/r/issues/9"
    assert result.error is None
    post_method, post_url, post_kwargs = client.calls[1]
    assert post_method == "POST"
    assert post_url == "https://api.github.com/repos/test-owner/test-repo/issues"
    assert post_kwargs["headers"]["Authorization"] == f"Bearer {settings.github_token}"
    assert post_kwargs["headers"]["Accept"] == "application/vnd.github+json"
    assert post_kwargs["json"]["title"] == "Bet title"
    assert "<!-- bet_id:bet-1 -->" in post_kwargs["json"]["body"]


def test_github_create_issue_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, payload: Any) -> _FakeResponse:
        if method == "GET":
            return _FakeResponse(200, [])
        return _FakeResponse(422, {"message": "Validation Failed"})

    _fake_httpx(monkeypatch, handler)

    result = github_mod.create_or_comment_issue(get_settings(), "bet-1", "t", "b")

    assert result.value is None
    assert result.error is not None
    assert result.error.source == Source.GITHUB
    assert result.error.severity == Severity.CRITICAL
    assert result.error.message == "github_error: 422"


def test_github_existing_issue_comments_instead(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, payload: Any) -> _FakeResponse:
        if method == "GET":
            marked = {
                "number": 7,
                "html_url": "https://github.com/o/r/issues/7",
                "body": "<!-- bet_id:bet-1 -->",
            }
            return _FakeResponse(200, [marked])
        return _FakeResponse(201, {})

    client = _fake_httpx(monkeypatch, handler)

    result = github_mod.create_or_comment_issue(get_settings(), "bet-1", "t", "b")

    assert result.value == "https://github.com/o/r/issues/7"
    assert result.error is None
    post_method, post_url, post_kwargs = client.calls[1]
    assert post_method == "POST"
    assert post_url == "https://api.github.com/repos/test-owner/test-repo/issues/7/comments"
    assert post_kwargs["json"] == {"body": "b"}


def test_github_comment_on_issue_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, payload: Any) -> _FakeResponse:
        return _FakeResponse(201, {})

    client = _fake_httpx(monkeypatch, handler)

    result = github_mod.comment_on_issue(
        get_settings(), "https://github.com/o/r/issues/42", "outcome text"
    )

    assert result.value is None
    assert result.error is None
    method, url, kwargs = client.calls[0]
    assert method == "POST"
    assert url == "https://api.github.com/repos/test-owner/test-repo/issues/42/comments"
    assert kwargs["json"] == {"body": "outcome text"}


def test_github_comment_on_issue_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, payload: Any) -> _FakeResponse:
        return _FakeResponse(404, {"message": "Not Found"})

    _fake_httpx(monkeypatch, handler)

    result = github_mod.comment_on_issue(get_settings(), "https://github.com/o/r/issues/42", "x")

    assert result.value is None
    assert result.error is not None
    assert result.error.source == Source.GITHUB
    assert result.error.message == "github_error: 404"


def test_github_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, payload: Any) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    _fake_httpx(monkeypatch, handler)

    result = github_mod.comment_on_issue(get_settings(), "https://github.com/o/r/issues/42", "x")

    assert result.value is None
    assert result.error is not None
    assert result.error.source == Source.GITHUB
    assert result.error.message.startswith("github_error:")


# --- gcal.py ---


def _fake_gcal(
    monkeypatch: pytest.MonkeyPatch,
    event_result: dict[str, Any] | None = None,
    execute_error: Exception | None = None,
) -> dict[str, Any]:
    """Install fake Credentials + discovery.build for gcal; return recorded calls."""
    recorded: dict[str, Any] = {}

    class FakeCredentials:
        @classmethod
        def from_service_account_file(cls, path: str) -> object:
            return object()

    class FakeInsertRequest:
        def execute(self) -> dict[str, Any]:
            if execute_error is not None:
                raise execute_error
            assert event_result is not None
            return event_result

    class FakeEvents:
        def insert(self, *, calendarId: str, body: dict[str, Any]) -> FakeInsertRequest:
            recorded["calendarId"] = calendarId
            recorded["body"] = body
            return FakeInsertRequest()

    class FakeService:
        def events(self) -> FakeEvents:
            return FakeEvents()

    def fake_build(service_name: str, version: str, **kwargs: Any) -> FakeService:
        recorded["build"] = (service_name, version, kwargs)
        return FakeService()

    monkeypatch.setattr(gcal_mod, "Credentials", FakeCredentials)
    monkeypatch.setattr(gcal_mod, "build", fake_build)
    return recorded


def test_gcal_create_event_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_gcal(monkeypatch, event_result={"id": "evt-123"})
    settings = get_settings()

    result = gcal_mod.create_review_event(
        settings, "2026-09-20", "Review bet: activation", "HogQL: SELECT ... bet_id:bet-1"
    )

    assert result.value == "evt-123"
    assert result.error is None
    assert calls["build"][:2] == ("calendar", "v3")
    assert calls["calendarId"] == settings.google_calendar_id
    assert calls["body"]["summary"] == "Review bet: activation"
    assert calls["body"]["description"] == "HogQL: SELECT ... bet_id:bet-1"
    assert calls["body"]["start"] == {"date": "2026-09-20"}
    assert calls["body"]["end"] == {"date": "2026-09-21"}


def test_gcal_create_event_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_gcal(monkeypatch, execute_error=RuntimeError("calendar down"))

    result = gcal_mod.create_review_event(get_settings(), "2026-09-20", "t", "d")

    assert result.value is None
    assert result.error is not None
    assert result.error.source == Source.GCAL
    assert result.error.severity == Severity.CRITICAL
    assert result.error.message.startswith("calendar_write_failed")

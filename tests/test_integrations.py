"""Integration tests (result objects; every failure branch -> exact DataFlag).

Google API mocked at the client boundary (monkeypatched in the gdocs module
namespace); no network, no SA file reads.
"""

from typing import Any

import pytest

import pm_agent.integrations.gdocs as gdocs
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

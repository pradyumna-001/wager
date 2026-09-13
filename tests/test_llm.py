"""Unit tests for the LLM gateway (ADR 0004).

The OpenAI SDK is mocked at the client level (monkeypatched in the
``pm_agent.llm`` namespace); no network in tests.
"""

from typing import Any

import pytest
from pydantic import BaseModel

import pm_agent.llm as llm_module
from pm_agent.config import get_settings
from pm_agent.errors import LLMValidationError


class _Out(BaseModel):
    name: str
    value: int


def _stub_client(
    monkeypatch: pytest.MonkeyPatch, contents: list[str | None]
) -> list[dict[str, Any]]:
    """Install a fake OpenAI client factory; return the recorded create() calls."""
    calls: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        content = contents.pop(0) if contents else None
        message = type("Message", (), {"content": content})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()

    completions = type("Completions", (), {})()
    completions.create = create
    chat = type("Chat", (), {})()
    chat.completions = completions
    client = type("Client", (), {})()
    client.chat = chat

    def fake_openai(**kwargs: Any) -> Any:
        assert "api_key" in kwargs and "base_url" in kwargs
        return client

    monkeypatch.setattr(llm_module, "OpenAI", fake_openai)
    return calls


def test_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_client(monkeypatch, ['{"name": "svc", "value": 1}'])
    settings = get_settings()

    out = llm_module.call_llm(settings, "system prompt", "user prompt", _Out)

    assert out == _Out(name="svc", value=1)
    assert len(calls) == 1
    call = calls[0]
    assert call["model"] == settings.llm_model
    assert call["temperature"] == 0.2
    assert call["response_format"] == {"type": "json_object"}
    assert call["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
    ]


def test_retry_once_with_error_appended(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_client(monkeypatch, ['{"name": "svc"}', '{"name": "svc", "value": 2}'])

    out = llm_module.call_llm(get_settings(), "sys", "original user prompt", _Out)

    assert out == _Out(name="svc", value=2)
    assert len(calls) == 2
    retry = calls[1]["messages"]
    assert retry[0] == {"role": "system", "content": "sys"}
    assert retry[1]["role"] == "user"
    assert retry[1]["content"].startswith("original user prompt")
    assert "Field required" in retry[1]["content"] or "value" in retry[1]["content"]


def test_second_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_client(monkeypatch, ["not json", '{"name": "svc"}'])

    with pytest.raises(LLMValidationError):
        llm_module.call_llm(get_settings(), "sys", "user", _Out)

    assert len(calls) == 2


def test_no_module_level_client() -> None:
    assert not hasattr(llm_module, "client")
    assert not hasattr(llm_module, "_client")

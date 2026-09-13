"""Smoke tests for M0.1 skeleton: config, errors, nodeutil."""

import pytest

from pm_agent.config import Settings, get_settings
from pm_agent.errors import (
    EmptyExtractionError,
    IntegrationError,
    LLMValidationError,
    PipelineError,
)
from pm_agent.nodeutil import add_node_checked, bet_id_ctx


class FakeGraph:
    """Minimal stand-in for a LangGraph StateGraph builder."""

    def __init__(self) -> None:
        self.nodes: dict[str, object] = {}

    def add_node(self, name: str, fn: object) -> None:
        self.nodes[name] = fn


def test_settings_loads() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.database_url == "postgresql://test:test@localhost:5432/pm_agent_test"
    assert settings.bet_tolerance_pp == 5.0
    assert settings.demo_mode is False


def test_settings_cache_clearable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "changed-model")
    get_settings.cache_clear()
    try:
        assert get_settings().llm_model == "changed-model"
    finally:
        get_settings.cache_clear()


def test_errors_hierarchy() -> None:
    assert issubclass(LLMValidationError, IntegrationError)
    assert issubclass(IntegrationError, Exception)
    assert issubclass(EmptyExtractionError, Exception)

    cause = ValueError("boom")
    err = PipelineError(phase="extract_facts", cause=cause)
    assert err.phase == "extract_facts"
    assert err.cause is cause
    assert "extract_facts" in str(err)


def test_add_node_checked_calls_fn() -> None:
    graph = FakeGraph()

    def node(state: dict) -> dict:
        return {**state, "ran": True}

    add_node_checked(graph, "dummy", node)
    fn = graph.nodes["dummy"]
    token = bet_id_ctx.set("bet-123")
    try:
        assert fn({"x": 1}) == {"x": 1, "ran": True}
    finally:
        bet_id_ctx.reset(token)


def test_add_node_checked_wraps_unexpected_exception() -> None:
    graph = FakeGraph()

    def boom(state: dict) -> dict:
        raise RuntimeError("kaput")

    add_node_checked(graph, "exploding", boom)
    with pytest.raises(PipelineError) as exc_info:
        graph.nodes["exploding"]({})
    assert exc_info.value.phase == "exploding"
    assert isinstance(exc_info.value.cause, RuntimeError)


def test_add_node_checked_passes_through_integration_error() -> None:
    graph = FakeGraph()

    def hard_stop(state: dict) -> dict:
        raise IntegrationError("gdocs down")

    add_node_checked(graph, "intake", hard_stop)
    with pytest.raises(IntegrationError):
        graph.nodes["intake"]({})

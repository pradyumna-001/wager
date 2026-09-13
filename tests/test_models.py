"""Schema/validation tests for models.py and state.py (no network)."""

from dataclasses import FrozenInstanceError
from datetime import UTC
from operator import add
from typing import get_args, get_origin
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pm_agent.models import (
    DataFlag,
    DerivedItem,
    DerivedReasoning,
    EdgeType,
    ExtractedFacts,
    GraphEdge,
    GraphNode,
    HypothesisItem,
    NodeStatus,
    NodeType,
    OptionItem,
    Severity,
    Source,
)
from pm_agent.state import BetState, create_initial_state

BET_ID = uuid4()


def _node(**overrides) -> GraphNode:
    return GraphNode(
        bet_id=BET_ID,
        type=NodeType.FACT,
        content="signups were 400 last week",
        source="meeting:2026-09-13:speaker=ana",
        **overrides,
    )


def test_node_type_enum_rejects_bad_value() -> None:
    with pytest.raises(ValidationError):
        GraphNode(bet_id=BET_ID, type="NOT_A_TYPE", content="x", source="s")


def test_node_status_defaults_active_and_validates() -> None:
    node = _node()
    assert node.status == NodeStatus.ACTIVE
    with pytest.raises(ValidationError):
        _node(status="ARCHIVED")


def test_confidence_bounds_and_optional() -> None:
    assert _node().confidence is None
    _node(confidence=0.75)
    with pytest.raises(ValidationError):
        _node(confidence=1.5)
    with pytest.raises(ValidationError):
        _node(confidence=-0.1)


def test_graph_edge_accepts_all_edge_types() -> None:
    for et in EdgeType:
        GraphEdge(bet_id=BET_ID, from_node=uuid4(), to_node=uuid4(), edge_type=et)


def test_dataflag_immutable_and_timestamped() -> None:
    flag = DataFlag(source=Source.POSTHOG, severity=Severity.CRITICAL, message="timeout")
    assert flag.created_at.tzinfo is not None
    assert flag.created_at.utcoffset() == UTC.utcoffset(flag.created_at)
    with pytest.raises(FrozenInstanceError):
        flag.message = "tampered"


def test_derived_reasoning_full_payload() -> None:
    payload = DerivedReasoning(
        assumptions=[DerivedItem(content="traffic stays flat", derived_from_fact_ids=[0])],
        hypotheses=[
            HypothesisItem(
                content="shortening signup raises completion",
                confidence=0.6,
                derived_from_fact_ids=[0, 1],
                depends_on_assumption_idxs=[0],
                metric_name="signup_completion_rate",
                metric_query="SELECT ... HogQL",
                predicted_lift=15.0,
                measurement_days=14,
            )
        ],
        options=[
            OptionItem(content="ship 2-step signup", derived_from_fact_ids=[1]),
            OptionItem(content="keep as-is", derived_from_fact_ids=[]),
        ],
        proposed_option_index=0,
    )
    assert payload.hypotheses[0].predicted_lift == 15.0


def test_derived_reasoning_requires_proposed_index() -> None:
    with pytest.raises(ValidationError):
        DerivedReasoning(assumptions=[], hypotheses=[], options=[])


def test_extracted_facts_shape() -> None:
    ef = ExtractedFacts(facts=[{"content": "churn is 5%", "speaker": "ana"}])
    assert ef.facts[0].speaker == "ana"


def test_create_initial_state_has_all_keys() -> None:
    state = create_initial_state("bet-1", "doc-1", "2026-09-13")
    expected_keys = set(BetState.__annotations__)
    assert set(state) == expected_keys
    for key in ("facts", "assumptions", "hypotheses", "options", "flags"):
        assert state[key] == []
    for key in expected_keys - {
        "bet_id",
        "doc_id",
        "meeting_date",
        "raw_transcript",
        "facts",
        "assumptions",
        "hypotheses",
        "options",
        "flags",
    }:
        assert state[key] is None
    assert state["raw_transcript"] == ""


def test_flags_uses_add_reducer() -> None:
    annotation = BetState.__annotations__["flags"]
    assert get_origin(annotation) is not None
    metadata = get_args(annotation)
    assert add in metadata


def test_reducer_appends_flags() -> None:
    f1 = DataFlag(source=Source.SLACK, severity=Severity.WARNING, message="a")
    f2 = DataFlag(source=Source.GITHUB, severity=Severity.INFO, message="b")
    merged = add([f1], [f2])
    assert merged == [f1, f2]

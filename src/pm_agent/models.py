"""Domain models per docs/02_DATA_MODEL.md + extraction payloads per docs/03.

Pydantic for schema-bearing models; DataFlag is a frozen dataclass (docs/01 §2).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class NodeType(StrEnum):
    FACT = "FACT"
    ASSUMPTION = "ASSUMPTION"
    HYPOTHESIS = "HYPOTHESIS"
    OPTION = "OPTION"
    DECISION = "DECISION"


class NodeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"


class EdgeType(StrEnum):
    DERIVED_FROM = "derived_from"  # ASSUMPTION/HYPOTHESIS/OPTION -> FACT
    SUPPORTS = "supports"  # outcome DECISION -> HYPOTHESIS (held)
    CONTRADICTS = "contradicts"  # outcome DECISION -> HYPOTHESIS (failed)
    LEADS_TO = "leads_to"  # chosen OPTION -> DECISION
    DEPENDS_ON = "depends_on"  # HYPOTHESIS -> ASSUMPTION


class Source(StrEnum):
    GDOCS = "gdocs"
    SLACK = "slack"
    GITHUB = "github"
    GCAL = "gcal"
    POSTHOG = "posthog"
    LLM = "llm"
    DB = "db"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class DataFlag:
    """Fail-visible record of an external failure. Persisted to bets.flags JSONB."""

    source: Source
    severity: Severity
    message: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class GraphNode(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    bet_id: UUID
    type: NodeType
    content: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source: str  # "meeting:<YYYY-MM-DD>:speaker=<name>" | "posthog:<query_id>" | "system"
    status: NodeStatus = NodeStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GraphEdge(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    bet_id: UUID  # denormalized for easy per-bet queries
    from_node: UUID
    to_node: UUID
    edge_type: EdgeType
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# --- Extraction LLM payloads (docs/03_PIPELINE_SPEC.md) ---


class FactItem(BaseModel):
    content: str
    speaker: str


class ExtractedFacts(BaseModel):
    facts: list[FactItem]


class DerivedItem(BaseModel):
    """An ASSUMPTION derived by the LLM."""

    content: str
    # SPEC-QUESTION: docs/03 names this derived_from_fact_ids but the LLM runs
    # before fact nodes have UUIDs; indices into the extraction list are used.
    derived_from_fact_ids: list[int]


class HypothesisItem(BaseModel):
    content: str
    confidence: float = Field(ge=0.0, le=1.0)
    derived_from_fact_ids: list[int]
    depends_on_assumption_idxs: list[int]
    metric_name: str
    metric_query: str  # HogQL SELECT
    predicted_lift: float  # percentage points
    measurement_days: int


class OptionItem(BaseModel):
    content: str
    derived_from_fact_ids: list[int]


class DerivedReasoning(BaseModel):
    assumptions: list[DerivedItem]
    hypotheses: list[HypothesisItem]
    options: list[OptionItem]
    proposed_option_index: int  # LLM's guess at the PM's choice

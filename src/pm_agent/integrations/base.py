"""Integration result objects (ADR 0005).

Every function in ``integrations/`` returns ``FetchResult`` — success carries
``value``; failure carries a typed ``DataFlag``. External failures never raise
across the integration boundary.
"""

from dataclasses import dataclass
from typing import Generic, TypeVar

from pm_agent.errors import IntegrationError
from pm_agent.models import DataFlag

# SPEC-QUESTION: docs/01 shows PEP 695 syntax (``class FetchResult[T]:``), which
# is Python 3.12+; pyproject targets py311, so TypeVar + Generic is used instead.
T = TypeVar("T")


@dataclass(frozen=True)
class FetchResult(Generic[T]):
    """Result of an external call. ``error`` is None on success."""

    value: T | None
    error: DataFlag | None


# SPEC-QUESTION: docs/01 layout lists IntegrationError in base.py as well, but
# errors.py (M0.1) already owns it; it is re-exported, not redefined, here.
__all__ = ["FetchResult", "IntegrationError"]

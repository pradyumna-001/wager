"""Central error types.

Integration functions return ``FetchResult(value, error)`` and never raise on
external failure; these exceptions are raised explicitly by nodes/hard stops
and translated by the graph/API layers.
"""


class IntegrationError(Exception):
    """An external integration (Slack/GitHub/GCal/GDocs/PostHog) failed hard."""


class LLMValidationError(IntegrationError):
    """LLM output failed structured validation or repeated repair attempts."""


class PipelineError(Exception):
    """Unexpected failure inside a graph node, wrapped by add_node_checked."""

    def __init__(self, phase: str, cause: BaseException) -> None:
        self.phase = phase
        self.cause = cause
        super().__init__(f"[{phase}] {type(cause).__name__}: {cause}")


class EmptyExtractionError(Exception):
    """Phase 1 extraction produced no usable nodes; nothing to confirm."""

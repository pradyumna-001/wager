"""Node registration wrapper: uniform logging, context, and error translation.

Every LangGraph node must be registered via ``add_node_checked`` so logging,
correlation contextvars, and error translation are uniform.

# SPEC-QUESTION: docs/01 §4 also describes appending a CRITICAL DataFlag on
# failure, but DataFlag models live in M1 (models.py). Until then the flag step
# is replaced by the structured error log line below.
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from langgraph.errors import GraphBubbleUp

from pm_agent.errors import IntegrationError, PipelineError

logger = logging.getLogger("pm_agent.node")

# Correlation contextvars; API middleware sets run_id, graph run sets bet_id.
bet_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("bet_id", default=None)
run_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)


def _structured_log(node: str, duration_ms: float, status: str) -> None:
    line = {
        "bet_id": bet_id_ctx.get(),
        "run_id": run_id_ctx.get(),
        "node": node,
        "duration_ms": round(duration_ms, 2),
        "status": status,
    }
    logger.info(json.dumps(line))


NodeFn = Callable[[dict[str, Any]], dict[str, Any]]


def add_node_checked(graph: Any, name: str, fn: NodeFn) -> None:
    """Register ``fn`` on ``graph`` with uniform logging and error translation.

    - Propagates ``bet_id``/``run_id`` contextvars into the node's execution.
    - Emits one structured timing log line per invocation.
    - Translates unexpected exceptions into ``PipelineError(phase=name, cause=...)``.
      ``PipelineError``/``IntegrationError`` pass through unchanged.
    """

    def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        # Copy at CALL time: the registration-time context lacks the runnable
        # config contextvars LangGraph sets (interrupt()/get_config() need them).
        ctx = contextvars.copy_context()
        start = time.perf_counter()
        try:
            result = ctx.run(fn, state)
        except (PipelineError, IntegrationError):
            elapsed = (time.perf_counter() - start) * 1000
            _structured_log(name, elapsed, "error")
            raise
        except GraphBubbleUp:
            # LangGraph control-flow exceptions (GraphInterrupt) must pass
            # through unchanged — they ARE the pause mechanism.
            elapsed = (time.perf_counter() - start) * 1000
            _structured_log(name, elapsed, "interrupt")
            raise
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            _structured_log(name, elapsed, "error")
            raise PipelineError(phase=name, cause=exc) from exc
        elapsed = (time.perf_counter() - start) * 1000
        _structured_log(name, elapsed, "ok")
        return result

    wrapped.__name__ = f"checked_{name}"
    graph.add_node(name, wrapped)

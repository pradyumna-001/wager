"""X-Run-Id correlation middleware (docs/01 §7)."""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from pm_agent.nodeutil import run_id_ctx


class RunIdMiddleware(BaseHTTPMiddleware):
    """Assign/propagate X-Run-Id and carry it into the nodeutil contextvars."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        run_id = request.headers.get("X-Run-Id") or uuid.uuid4().hex
        run_id_ctx.set(run_id)
        response = await call_next(request)
        response.headers["X-Run-Id"] = run_id
        return response

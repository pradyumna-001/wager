"""FastAPI app factory (docs/01 §5).

No side effects at import beyond the factory call below.
"""

from __future__ import annotations

from fastapi import FastAPI

from pm_agent.api.errors import register_error_handlers
from pm_agent.api.middleware import RunIdMiddleware
from pm_agent.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="pm-agent")
    app.add_middleware(RunIdMiddleware)
    app.include_router(router)
    register_error_handlers(app)
    return app


app = create_app()

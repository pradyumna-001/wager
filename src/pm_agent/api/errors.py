"""Central API error contract (docs/01 §5).

Every failure leaves the API as ``{"error": {"code": ..., "message": ...}}`` —
no ad-hoc HTTPExceptions scattered around routes.
"""

from __future__ import annotations

from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pm_agent.errors import PipelineError


class ErrorCode(StrEnum):
    GDOCS_FAILED = "gdocs_fetch_failed"
    LLM_FAILED = "llm_failed"
    EMPTY_EXTRACTION = "empty_extraction"
    BET_NOT_FOUND = "bet_not_found"
    BAD_SIGNATURE = "bad_signature"
    PIPELINE_ERROR = "pipeline_error"
    ALREADY_PENDING = "already_pending"
    INTERNAL_ERROR = "internal_error"


class ApiError(Exception):
    """Domain error carrying a machine-readable code, message and HTTP status."""

    def __init__(self, code: ErrorCode | str, message: str, status: int = 400) -> None:
        self.code = str(code)
        self.message = message
        self.status = status
        super().__init__(f"[{self.code}] {message}")


def _payload(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def register_error_handlers(app: FastAPI) -> None:
    """Translate ApiError | PipelineError | Exception into the error envelope."""

    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=_payload(exc.code, exc.message))

    @app.exception_handler(PipelineError)
    async def _pipeline_error_handler(request: Request, exc: PipelineError) -> JSONResponse:
        return JSONResponse(
            status_code=500, content=_payload(ErrorCode.PIPELINE_ERROR.value, str(exc))
        )

    @app.exception_handler(Exception)
    async def _internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500, content=_payload(ErrorCode.INTERNAL_ERROR.value, str(exc))
        )

"""Exception handlers producing the standard error envelope.

Internal exception messages are never returned to clients; they are logged
(with redaction) and correlated through the request ID.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import get_request_id
from app.schemas.common import ErrorDetail, ErrorResponse

logger = logging.getLogger("app.errors")

_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    503: "service_unavailable",
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details, request_id=get_request_id())
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


async def http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101 - registered for this type only
    code = _STATUS_CODES.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    headers = dict(exc.headers) if exc.headers else None
    return error_response(exc.status_code, code, message, headers=headers)


async def validation_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101 - registered for this type only
    # Drop submitted input values from the response; they may contain PII.
    details = [
        {k: v for k, v in err.items() if k in ("type", "loc", "msg")} for err in exc.errors()
    ]
    return error_response(
        422, "validation_error", "Request validation failed", jsonable_encoder(details)
    )


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler (Starlette's ServerErrorMiddleware, outermost layer)."""
    logger.error("unhandled exception", exc_info=(type(exc), exc, exc.__traceback__))
    return error_response(500, "internal_error", "An unexpected error occurred")


class UnhandledExceptionMiddleware:
    """Innermost middleware converting unexpected exceptions into a 500 envelope.

    Starlette routes ``Exception`` handlers to ``ServerErrorMiddleware``, which sits
    *outside* all user middleware, so its responses would miss the request ID,
    CORS and security headers. Catching here keeps those layers in the path.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if response_started:  # cannot send a new response mid-stream
                raise
            logger.error("unhandled exception", exc_info=(type(exc), exc, exc.__traceback__))
            response = error_response(500, "internal_error", "An unexpected error occurred")
            await response(scope, receive, send)


async def database_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Connection/operational database failures -> 503 (details only in the log)."""
    logger.error(
        "database unavailable",
        extra={"error_type": type(exc).__name__},
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return error_response(
        503, "database_unavailable", "The database is temporarily unavailable. Please retry."
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    # IntegrityError is handled where it means something (e.g. 409 duplicates).
    for exc_type in (OperationalError, InterfaceError, DBAPIError):
        app.add_exception_handler(exc_type, database_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

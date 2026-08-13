from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Expected application error that can be safely returned to API clients."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        content["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=content, headers=headers)


async def app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
        headers=exc.headers,
    )


async def http_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    message = exc.detail if isinstance(exc.detail, str) else "The request could not be completed"
    details = None if isinstance(exc.detail, str) else exc.detail
    return error_response(
        status_code=exc.status_code,
        code=f"http_{exc.status_code}",
        message=message,
        details=details,
    )


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return error_response(
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        details=jsonable_encoder(exc.errors()),
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error while processing %s", request.url.path, exc_info=exc)
    return error_response(
        status_code=500,
        code="internal_server_error",
        message="An unexpected error occurred",
    )


ExceptionHandler = Callable[[Request, Exception], Awaitable[JSONResponse]]


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)

"""Public error responses omit raw inputs and private exception details."""

from http import HTTPStatus
from typing import Final
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException
from starlette.responses import Response

from jobtology_be.application.services.analysis_inputs import (
    AnalysisContextInputsUnavailableError,
)
from jobtology_be.infrastructure.persistence.contracts import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    MissingRecordError,
    OwnershipError,
    PersistenceConflictError,
)


class ValidationIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    location: tuple[str | int, ...]
    code: str


class ErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    details: tuple[ValidationIssue, ...] = ()
    request_id: UUID = Field(default_factory=uuid4)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: ErrorBody


HTTP_ERROR_CODES: Final = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    410: "GONE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "DATA_UNAVAILABLE",
}


async def validation_error_handler(request: Request, exc: RequestValidationError) -> Response:
    body = ErrorResponse(
        error=ErrorBody(
            code="VALIDATION_ERROR",
            message="Request validation failed",
            details=tuple(
                ValidationIssue(location=error["loc"], code=error["type"]) for error in exc.errors()
            ),
        )
    )
    return Response(
        content=body.model_dump_json(),
        status_code=422,
        media_type="application/json",
        headers={"X-Request-ID": str(body.error.request_id)},
    )


async def http_error_handler(request: Request, exc: HTTPException) -> Response:
    try:
        message = HTTPStatus(exc.status_code).phrase
    except ValueError:
        message = "HTTP request failed"
    body = ErrorResponse(
        error=ErrorBody(
            code=HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR"),
            message=message,
        )
    )
    response = Response(
        content=body.model_dump_json(),
        status_code=exc.status_code,
        media_type="application/json",
        headers=exc.headers,
    )
    response.headers["X-Request-ID"] = str(body.error.request_id)
    return response


def _product_error_response(status_code: int, code: str, message: str) -> Response:
    body = ErrorResponse(error=ErrorBody(code=code, message=message))
    return Response(
        content=body.model_dump_json(),
        status_code=status_code,
        media_type="application/json",
        headers={"X-Request-ID": str(body.error.request_id)},
    )


async def persistence_conflict_error_handler(
    _request: Request, _exc: PersistenceConflictError
) -> Response:
    return _product_error_response(409, "VERSION_CONFLICT", "Request conflicts with current state")


async def idempotency_conflict_error_handler(
    _request: Request, _exc: IdempotencyConflictError
) -> Response:
    return _product_error_response(409, "IDEMPOTENCY_CONFLICT", "Idempotency key was reused")


async def idempotency_in_progress_error_handler(
    _request: Request, _exc: IdempotencyInProgressError
) -> Response:
    return _product_error_response(409, "IDEMPOTENCY_IN_PROGRESS", "Idempotency request is in progress")


async def ownership_error_handler(_request: Request, _exc: OwnershipError) -> Response:
    return _product_error_response(403, "FORBIDDEN", "Request is not permitted")


async def missing_record_error_handler(_request: Request, _exc: MissingRecordError) -> Response:
    return _product_error_response(404, "NOT_FOUND", "Requested resource was not found")


async def analysis_context_inputs_unavailable_error_handler(
    _request: Request, _exc: AnalysisContextInputsUnavailableError
) -> Response:
    return _product_error_response(503, "DATA_UNAVAILABLE", "Analysis inputs are unavailable")


def register_error_handlers(app: FastAPI) -> None:
    app.exception_handler(RequestValidationError)(validation_error_handler)
    app.exception_handler(HTTPException)(http_error_handler)
    app.exception_handler(PersistenceConflictError)(persistence_conflict_error_handler)
    app.exception_handler(IdempotencyConflictError)(idempotency_conflict_error_handler)
    app.exception_handler(IdempotencyInProgressError)(idempotency_in_progress_error_handler)
    app.exception_handler(OwnershipError)(ownership_error_handler)
    app.exception_handler(MissingRecordError)(missing_record_error_handler)
    app.exception_handler(AnalysisContextInputsUnavailableError)(
        analysis_context_inputs_unavailable_error_handler
    )

import asyncio
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from jobtology_be.api.chat import require_chat_rate_limiter
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.infrastructure.persistence.rate_limit import (
    AccountRateLimiter,
    RateLimitUnavailableError,
)
from jobtology_be.llm.client import LlmUnavailableError
from jobtology_be.resume.models import (
    MAX_BYTES,
    MAX_PAGES,
    PARSE_SECONDS,
    ResumeFormat,
    ResumeInterpretRequest,
    ResumeInterpretResponse,
    ResumeScan,
)
from jobtology_be.resume.service import ResumeError, ResumeService

router = APIRouter(tags=['resume'])


def require_resume_service() -> ResumeService:
    raise HTTPException(status_code=503)


async def _quota(limiter: AccountRateLimiter | None, principal: AuthenticatedPrincipal):
    if limiter is None:
        raise HTTPException(status_code=503)
    try:
        decision = await limiter.consume(principal.user_id)
    except RateLimitUnavailableError:
        raise HTTPException(status_code=503) from None
    if not decision.allowed:
        raise HTTPException(status_code=429, headers={'Retry-After': str(decision.retry_after_seconds)})


def _error(error: ResumeError):
    request_id = str(uuid4())
    return JSONResponse(status_code=error.status, content={'error': {
        'code': error.code, 'message': 'Resume processing could not complete',
        'request_id': request_id, 'details': [],
    }}, headers={'Cache-Control': 'no-store', 'X-Request-ID': request_id})


@router.get('/me/resume/status')
async def status(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[ResumeService, Depends(require_resume_service)],
    response: Response,
):
    response.headers['Cache-Control'] = 'no-store'
    return {
        'formats': await asyncio.to_thread(lambda: service.formats), 'max_bytes': MAX_BYTES,
        'max_pdf_pages': MAX_PAGES, 'parse_timeout_seconds': PARSE_SECONDS,
        'ai_available': service.llm is not None, 'ai_provider': service.provider_label,
        'retention': 'REQUEST_ONLY',
    }


@router.post('/me/resume/scan', response_model=ResumeScan)
async def scan(
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[ResumeService, Depends(require_resume_service)],
    limiter: Annotated[AccountRateLimiter | None, Depends(require_chat_rate_limiter)],
    response: Response,
    format: Annotated[ResumeFormat, Query()],
    occupation_id: str | None = Query(default=None, max_length=128),
):
    """Raw file body; filenames never enter URLs, logs or storage. Session CSRF applies."""
    response.headers['Cache-Control'] = 'no-store'
    await _quota(limiter, principal)
    length = request.headers.get('content-length')
    if length and (not length.isdigit() or len(length) > 20 or int(length) > MAX_BYTES):
        return _error(ResumeError('FILE_TOO_LARGE', 413))
    try:
        return await service.scan(principal.user_id, format, request.stream(), occupation_id,
                                  request.is_disconnected)
    except ResumeError as error:
        return _error(error)
    except ClientDisconnect:
        return Response(status_code=499, headers={'Cache-Control': 'no-store'})


@router.post('/me/resume/interpret', response_model=ResumeInterpretResponse)
async def interpret(
    request: ResumeInterpretRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[ResumeService, Depends(require_resume_service)],
    limiter: Annotated[AccountRateLimiter | None, Depends(require_chat_rate_limiter)],
    response: Response,
):
    """Explicit fallback: only the user-reviewed passage is sent to the configured model."""
    response.headers['Cache-Control'] = 'no-store'
    await _quota(limiter, principal)
    try:
        return await service.interpret(request.text, request.occupation_id)
    except ResumeError as error:
        return _error(error)
    except LlmUnavailableError:
        return _error(ResumeError('AI_UNAVAILABLE', 503))

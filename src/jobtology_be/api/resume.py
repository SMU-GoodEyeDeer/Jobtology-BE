import asyncio
import json
import logging
import time
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, StreamingResponse

from jobtology_be.api.chat import require_chat_rate_limiter
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.infrastructure.persistence.rate_limit import (
    AccountRateLimiter,
    RateLimitUnavailableError,
)
from jobtology_be.llm.client import LlmUnavailableError
from jobtology_be.resume.models import (
    AI_CONCURRENCY,
    MAX_AI_BLOCKS,
    MAX_AI_TEXT,
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
_logger = logging.getLogger(__name__)


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


def _error(error: ResumeError, *, operation: str, started: float, format: str | None = None):
    request_id = str(uuid4())
    # Never log filenames, document text, hashes, user IDs or exception messages.
    _logger.warning(
        'resume_processing_failed request_id=%s operation=%s format=%s code=%s '
        'status=%s elapsed_ms=%d parser_exit_code=%s parser_error_type=%s',
        request_id, operation, format, error.code, error.status,
        (time.monotonic() - started) * 1000, error.parser_exit_code, error.parser_error_type,
    )
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
        'ai_formats': ['pdf'], 'ai_max_paragraphs': MAX_AI_BLOCKS, 'ai_max_characters': MAX_AI_TEXT,
        'ai_concurrency': AI_CONCURRENCY,
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
    started = time.monotonic()
    await _quota(limiter, principal)
    length = request.headers.get('content-length')
    if length and (not length.isdigit() or len(length) > 20 or int(length) > MAX_BYTES):
        return _error(ResumeError('FILE_TOO_LARGE', 413), operation='scan', started=started, format=format)
    try:
        return await service.scan(principal.user_id, format, request.stream(), occupation_id,
                                  request.is_disconnected)
    except ResumeError as error:
        return _error(error, operation='scan', started=started, format=format)
    except ClientDisconnect:
        return Response(status_code=499, headers={'Cache-Control': 'no-store'})


@router.post('/me/resume/analyze', response_class=StreamingResponse)
async def analyze(
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[ResumeService, Depends(require_resume_service)],
    limiter: Annotated[AccountRateLimiter | None, Depends(require_chat_rate_limiter)],
    format: Annotated[ResumeFormat, Query()] = 'pdf',
    ai_consent: Annotated[bool, Query()] = False,
    occupation_id: Annotated[str | None, Query(max_length=128)] = None,
):
    """One structured call per paragraph, concurrently; no classification stage or account writes."""
    started = time.monotonic()
    await _quota(limiter, principal)
    try:
        if format != 'pdf':
            raise ResumeError('AI_PDF_REQUIRED')
        if not ai_consent:
            raise ResumeError('AI_CONSENT_REQUIRED')
        if service.llm is None:
            raise ResumeError('AI_UNAVAILABLE', 503)
        length = request.headers.get('content-length')
        if length and (not length.isdigit() or len(length) > 20 or int(length) > MAX_BYTES):
            raise ResumeError('FILE_TOO_LARGE', 413)
        # Consume the upload before StreamingResponse starts listening for disconnects.
        # The parser enforces three actual PDF pages before any model request.
        scan = await service.scan(principal.user_id, format, request.stream(), occupation_id,
                                  request.is_disconnected, include_keywords=False)
        service.validate_analysis(scan)
    except ResumeError as error:
        return _error(error, operation='analyze', started=started, format=format)
    except ClientDisconnect:
        return Response(status_code=499, headers={'Cache-Control': 'no-store'})

    async def events():
        try:
            async for event in service.analyze_events(
                principal.user_id, scan, occupation_id, request.is_disconnected,
            ):
                yield json.dumps(event, ensure_ascii=False) + '\n'
        except ResumeError as error:
            if error.code == 'CLIENT_DISCONNECTED':
                return
            response = _error(error, operation='analyze', started=started, format=format)
            yield json.dumps({'type': 'error', **json.loads(response.body)}) + '\n'

    return StreamingResponse(events(), media_type='application/x-ndjson', headers={
        'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no',
    })


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
    started = time.monotonic()
    await _quota(limiter, principal)
    try:
        return await service.interpret(request.text, request.occupation_id)
    except ResumeError as error:
        return _error(error, operation='interpret', started=started)
    except LlmUnavailableError:
        return _error(ResumeError('AI_UNAVAILABLE', 503), operation='interpret', started=started)

from datetime import date
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from jobtology_be.api.errors import ErrorResponse
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.live_source_models import (
    ExamSessionsResponse,
    PostingResponse,
    PostingsResponse,
)
from jobtology_be.infrastructure.persistence.live_source_feed import (
    LiveSourceFeed,
    LiveSourceReadError,
)

router = APIRouter(prefix="/live", tags=["live source feed"])
_RESPONSES: Final[dict[int | str, dict[str, object]]] = {
    401: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
    422: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
}


async def require_live_source_feed() -> LiveSourceFeed:
    raise HTTPException(status_code=503)


def _allowed_query(request: Request, allowed: frozenset[str]) -> None:
    params = request.query_params
    if set(params) - allowed or any(len(params.getlist(key)) != 1 for key in params):
        raise HTTPException(status_code=422)


@router.get("/postings", response_model=PostingsResponse, responses=_RESPONSES)
async def postings(
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    feed: Annotated[LiveSourceFeed, Depends(require_live_source_feed)],
    q: str | None = None,
    ncs_category: str | None = None,
    region: str | None = None,
    open_on: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PostingsResponse:
    _allowed_query(request, frozenset({"q", "ncs_category", "region", "open_on", "limit", "offset"}))
    try:
        return await feed.postings(q, ncs_category, region, open_on, limit, offset)
    except LiveSourceReadError as error:
        raise HTTPException(status_code=error.status_code) from None


@router.get("/postings/{posting_id:path}", response_model=PostingResponse, responses=_RESPONSES)
async def posting(
    posting_id: str,
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    feed: Annotated[LiveSourceFeed, Depends(require_live_source_feed)],
) -> PostingResponse:
    _allowed_query(request, frozenset())
    if not posting_id:
        raise HTTPException(status_code=422)
    try:
        return await feed.posting(posting_id)
    except LiveSourceReadError as error:
        raise HTTPException(status_code=error.status_code) from None


@router.get("/exam-sessions", response_model=ExamSessionsResponse, responses=_RESPONSES)
async def exam_sessions(
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    feed: Annotated[LiveSourceFeed, Depends(require_live_source_feed)],
    qualification: str | None = None,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExamSessionsResponse:
    _allowed_query(request, frozenset({"qualification", "from", "to", "limit", "offset"}))
    try:
        return await feed.exam_sessions(qualification, from_date, to_date, limit, offset)
    except LiveSourceReadError as error:
        raise HTTPException(status_code=error.status_code) from None

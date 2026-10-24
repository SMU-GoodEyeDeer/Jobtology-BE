import re
from datetime import date
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from jobtology_be.api.chat import require_chat_workspace
from jobtology_be.api.errors import ErrorResponse
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.live_source_models import (
    CrosswalkResponse,
    ExamSessionsResponse,
    NcsDemandResponse,
    NcsUnitResponse,
    OccupationContextResponse,
    PostingResponse,
    PostingsResponse,
)
from jobtology_be.chat.crosswalk import crosswalk_graph
from jobtology_be.chat.workspace import ChatWorkspaceService
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


@router.get("/crosswalk", response_model=CrosswalkResponse, responses=_RESPONSES)
async def crosswalk(
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    feed: Annotated[LiveSourceFeed, Depends(require_live_source_feed)],
    workspace: Annotated[ChatWorkspaceService | None, Depends(require_chat_workspace)],
    node_id: Annotated[str, Query(min_length=1, max_length=500)],
    context_nodes: Annotated[str | None, Query(max_length=8000)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CrosswalkResponse:
    """Versioned catalog mappings for exploration; never automatic competency credit."""
    _allowed_query(request, frozenset({"node_id", "limit", "offset", "context_nodes"}))
    context_ids = tuple(dict.fromkeys(context_nodes.split(","))) if context_nodes else ()
    if len(context_ids) > 48 or any(not value or len(value) > 500 for value in context_ids):
        raise HTTPException(status_code=422)
    try:
        result = await feed.crosswalk(node_id, limit, offset)
        connections = getattr(workspace.catalog, "crosswalk_connections", None) if workspace else None
        graph = (await connections(result, node_id, context_ids) if connections
                 else crosswalk_graph(result, node_id, context_ids))
        return result.model_copy(update={"graph": graph})
    except LiveSourceReadError as error:
        raise HTTPException(status_code=error.status_code) from None


@router.get("/occupation-context", response_model=OccupationContextResponse, responses=_RESPONSES)
async def occupation_context(
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    feed: Annotated[LiveSourceFeed, Depends(require_live_source_feed)],
    occupation_codes: Annotated[str | None, Query(max_length=1500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OccupationContextResponse:
    """Inferred occupation exposure with exact source/review pins; never competency credit."""
    _allowed_query(request, frozenset({"occupation_codes", "limit", "offset"}))
    codes = occupation_codes.split(",") if occupation_codes is not None else None
    if codes is not None and (len(codes) > 100 or any(
        re.fullmatch(r"(?:[0-9]{8}|[0-9]{2}-[0-9]{4}\.[0-9]{2})", code) is None for code in codes
    )):
        raise HTTPException(status_code=422)
    try:
        return await feed.occupation_context(codes, limit, offset)
    except LiveSourceReadError as error:
        raise HTTPException(status_code=error.status_code) from None


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


@router.get("/ncs-units/{base_code}", response_model=NcsUnitResponse, responses=_RESPONSES)
async def ncs_unit(
    base_code: Annotated[str, Path(pattern=r"^[0-9]{10}$")],
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    feed: Annotated[LiveSourceFeed, Depends(require_live_source_feed)],
) -> NcsUnitResponse:
    _allowed_query(request, frozenset())
    try:
        return await feed.ncs_unit(base_code)
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


@router.get("/ncs-demand", response_model=NcsDemandResponse, responses=_RESPONSES)
async def ncs_demand(
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    feed: Annotated[LiveSourceFeed, Depends(require_live_source_feed)],
    ncs_prefix: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NcsDemandResponse:
    _allowed_query(request, frozenset({"ncs_prefix", "limit", "offset"}))
    try:
        return await feed.ncs_demand(ncs_prefix, limit, offset)
    except LiveSourceReadError as error:
        raise HTTPException(status_code=error.status_code) from None

from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from jobtology_be.api.errors import ErrorResponse
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.source_catalog_models import (
    CatalogEntities,
    CatalogEntity,
    CatalogRelations,
    CatalogSummary,
    EntityKind,
)
from jobtology_be.infrastructure.persistence.source_catalog import CatalogQueries, CatalogReadError

router = APIRouter(prefix="/catalog", tags=["source catalog"])
_RESPONSES: Final[dict[int | str, dict[str, object]]] = {
    401: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
    410: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
_ALIASES: Final[dict[str, EntityKind]] = {
    "occupations": "occupation", "competencies": "ncsCompetency",
    "organizations": "organization", "postings": "jobPosting",
    "qualifications": "qualification", "exam-sessions": "examSession",
    "career-ranks": "careerRank",
}


async def require_source_catalog() -> CatalogQueries:
    raise HTTPException(status_code=503)


def _allowed_query(request: Request, allowed: frozenset[str]) -> None:
    params = request.query_params
    if (set(params) - allowed or any(len(params.getlist(key)) != 1 for key in params)
            or ("release_id" in params and not params["release_id"].strip())):
        raise HTTPException(status_code=422)


@router.get("/summary", response_model=CatalogSummary, responses=_RESPONSES)
async def summary(
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    catalog: Annotated[CatalogQueries, Depends(require_source_catalog)],
    release_id: str | None = None,
) -> CatalogSummary:
    _allowed_query(request, frozenset({"release_id"}))
    try:
        return CatalogSummary.model_validate(await catalog.summary(release_id))
    except CatalogReadError as error:
        raise HTTPException(status_code=error.status_code) from None


@router.get("/entities", response_model=CatalogEntities, responses=_RESPONSES)
async def entities(
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    catalog: Annotated[CatalogQueries, Depends(require_source_catalog)],
    kind: EntityKind | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    release_id: str | None = None,
) -> CatalogEntities:
    _allowed_query(request, frozenset({"kind", "limit", "offset", "release_id"}))
    try:
        return CatalogEntities.model_validate(await catalog.entities(release_id, kind, limit, offset))
    except CatalogReadError as error:
        raise HTTPException(status_code=error.status_code) from None


@router.get("/relations", response_model=CatalogRelations, responses=_RESPONSES)
async def relations(
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    catalog: Annotated[CatalogQueries, Depends(require_source_catalog)],
    entity_id: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    release_id: str | None = None,
) -> CatalogRelations:
    _allowed_query(request, frozenset({"entity_id", "limit", "offset", "release_id"}))
    try:
        return CatalogRelations.model_validate(
            await catalog.relations(release_id, entity_id, limit, offset)
        )
    except CatalogReadError as error:
        raise HTTPException(status_code=error.status_code) from None


@router.get("/{alias}", response_model=CatalogEntities, responses=_RESPONSES)
async def list_alias(
    alias: str,
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    catalog: Annotated[CatalogQueries, Depends(require_source_catalog)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    release_id: str | None = None,
) -> CatalogEntities:
    _allowed_query(request, frozenset({"limit", "offset", "release_id"}))
    kind = _ALIASES.get(alias)
    if kind is None:
        raise HTTPException(status_code=404)
    try:
        return CatalogEntities.model_validate(await catalog.entities(release_id, kind, limit, offset))
    except CatalogReadError as error:
        raise HTTPException(status_code=error.status_code) from None


@router.get("/entities/{entity_id:path}", response_model=CatalogEntity, responses=_RESPONSES)
async def entity(
    entity_id: str,
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    catalog: Annotated[CatalogQueries, Depends(require_source_catalog)],
    release_id: str | None = None,
) -> CatalogEntity:
    _allowed_query(request, frozenset({"release_id"}))
    if not entity_id:
        raise HTTPException(status_code=422)
    try:
        return CatalogEntity.model_validate(await catalog.entity(release_id, entity_id))
    except CatalogReadError as error:
        raise HTTPException(status_code=error.status_code) from None

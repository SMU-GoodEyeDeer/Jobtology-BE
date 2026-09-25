from typing import Annotated, Final, NoReturn, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query

from jobtology_be.api.errors import ErrorResponse
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.occupation_source_models import (
    Neo4jNcsAlignmentResponse,
    Neo4jOccupationResponse,
    Neo4jPublicationResponse,
)
from jobtology_be.corpus.neo4j_client import Neo4jQueryApiError
from jobtology_be.corpus.neo4j_models import (
    Neo4jNcsAlignment,
    Neo4jNcsCompetencyNode,
    Neo4jOccupationNode,
    Neo4jPublication,
)
from jobtology_be.corpus.neo4j_repository import (
    Neo4jNcsAlignmentSource,
    Neo4jPagination,
    Neo4jPublicationNotFoundError,
    Neo4jRepositoryError,
    Neo4jRepositoryRequestError,
)
from jobtology_be.corpus.source_availability import Available, SourceCapabilities

router = APIRouter(tags=["native catalog"])

_NATIVE_CATALOG_PAGE_LIMIT: Final = 100

_READ_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Authentication is required."},
    422: {"model": ErrorResponse, "description": "Native catalog request parameters are invalid."},
    503: {"model": ErrorResponse, "description": "The native source catalog is unavailable."},
}

_ALIGNMENT_READ_RESPONSES = _READ_RESPONSES | {
    404: {"model": ErrorResponse, "description": "The requested native publication is absent."},
}


class Neo4jCatalogAlignment(Protocol):
    @property
    def source_enrichment(self) -> Neo4jNcsAlignmentSource: ...

    @property
    def alignment(self) -> Neo4jNcsAlignment: ...

    @property
    def competency(self) -> Neo4jNcsCompetencyNode: ...


class Neo4jCatalogQueries(Protocol):
    @property
    def capabilities(self) -> SourceCapabilities: ...

    async def list_occupations(
        self, *, page: Neo4jPagination
    ) -> tuple[Neo4jOccupationNode, ...]: ...

    async def list_publications(self, *, page: Neo4jPagination) -> tuple[Neo4jPublication, ...]: ...

    async def list_alignments(
        self, publication_id: str, *, page: Neo4jPagination
    ) -> tuple[Neo4jCatalogAlignment, ...]: ...


async def require_neo4j_catalog_queries() -> Neo4jCatalogQueries:
    raise HTTPException(status_code=503)


@router.get(
    "/occupations",
    response_model=tuple[Neo4jOccupationResponse, ...],
    responses=_READ_RESPONSES,
    summary="List native Neo4j occupations",
    description=(
        "Lists the source catalog's safe occupation fields. This does not map source occupations "
        "to the application's editorial occupation IDs."
    ),
)
async def list_occupations(
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    catalog: Annotated[Neo4jCatalogQueries, Depends(require_neo4j_catalog_queries)],
    limit: Annotated[int, Query(ge=1, le=_NATIVE_CATALOG_PAGE_LIMIT)] = _NATIVE_CATALOG_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> tuple[Neo4jOccupationResponse, ...]:
    _require_catalog_available(catalog)
    try:
        occupations = await catalog.list_occupations(page=Neo4jPagination(limit=limit, offset=offset))
    except (Neo4jRepositoryError, Neo4jQueryApiError) as error:
        _raise_catalog_error(error)
    return tuple(Neo4jOccupationResponse.from_source(occupation) for occupation in occupations)


@router.get(
    "/publications",
    response_model=tuple[Neo4jPublicationResponse, ...],
    responses=_READ_RESPONSES,
    summary="List native Neo4j publications",
    description=(
        "Lists source publication IDs and source-native states. Source state is not an editorial "
        "publication state."
    ),
)
async def list_publications(
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    catalog: Annotated[Neo4jCatalogQueries, Depends(require_neo4j_catalog_queries)],
    limit: Annotated[int, Query(ge=1, le=_NATIVE_CATALOG_PAGE_LIMIT)] = _NATIVE_CATALOG_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> tuple[Neo4jPublicationResponse, ...]:
    _require_catalog_available(catalog)
    try:
        publications = await catalog.list_publications(page=Neo4jPagination(limit=limit, offset=offset))
    except (Neo4jRepositoryError, Neo4jQueryApiError) as error:
        _raise_catalog_error(error)
    return tuple(
        Neo4jPublicationResponse.from_source(publication, catalog.capabilities)
        for publication in publications
    )


@router.get(
    "/publications/{publication_id}/alignments",
    response_model=tuple[Neo4jNcsAlignmentResponse, ...],
    responses=_ALIGNMENT_READ_RESPONSES,
    summary="List native NCS alignments for a publication",
    description=(
        "Lists safe source-enrichment context, source alignment decisions, and competency labels. "
        "An accepted source alignment is not a user capability, occupational requirement, or planning outcome."
    ),
)
async def list_publication_alignments(
    publication_id: str,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    catalog: Annotated[Neo4jCatalogQueries, Depends(require_neo4j_catalog_queries)],
    limit: Annotated[int, Query(ge=1, le=_NATIVE_CATALOG_PAGE_LIMIT)] = _NATIVE_CATALOG_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> tuple[Neo4jNcsAlignmentResponse, ...]:
    _require_catalog_available(catalog)
    try:
        alignments = await catalog.list_alignments(
            publication_id,
            page=Neo4jPagination(limit=limit, offset=offset),
        )
    except (Neo4jRepositoryError, Neo4jQueryApiError) as error:
        _raise_catalog_error(error)
    if any(item.alignment.publication_id != publication_id for item in alignments):
        raise HTTPException(status_code=503)
    return tuple(
        Neo4jNcsAlignmentResponse.from_source(
            item.source_enrichment,
            item.alignment,
            item.competency,
        )
        for item in alignments
    )


def _require_catalog_available(catalog: Neo4jCatalogQueries) -> None:
    capabilities = catalog.capabilities
    if capabilities.source != "neo4j_query_api" or not isinstance(capabilities.catalog, Available):
        raise HTTPException(status_code=503)


def _raise_catalog_error(error: Neo4jRepositoryError | Neo4jQueryApiError) -> NoReturn:
    if isinstance(error, Neo4jRepositoryRequestError):
        raise HTTPException(status_code=422) from error
    if isinstance(error, Neo4jPublicationNotFoundError):
        raise HTTPException(status_code=404) from error
    raise HTTPException(status_code=503) from error

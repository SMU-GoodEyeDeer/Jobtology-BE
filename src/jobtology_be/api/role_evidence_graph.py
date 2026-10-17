from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException

from jobtology_be.api.errors import ErrorResponse
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.corpus.neo4j_client import Neo4jQueryApiError
from jobtology_be.corpus.neo4j_repository import Neo4jRepositoryError
from jobtology_be.corpus.role_evidence_graph import RoleEvidenceGraph

router = APIRouter(tags=["native catalog"])


class RoleEvidenceGraphSource(Protocol):
    async def graph(self, occupation_id: str) -> RoleEvidenceGraph | None: ...


async def require_role_evidence_graph() -> RoleEvidenceGraphSource:
    raise HTTPException(status_code=503)


@router.get(
    "/roles/{occupation_id}/evidence-graph",
    response_model=RoleEvidenceGraph,
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        404: {"model": ErrorResponse, "description": "The product role is unknown."},
        503: {"model": ErrorResponse, "description": "The native Neo4j graph is unavailable."},
    },
    summary="Read a product role's recommendation-evidence graph from Neo4j",
    description=(
        "Returns a bounded graph for one product role: its mapped NCS occupations, "
        "model-inferred posting counts, NCS competency units, and the O*NET occupations, "
        "essential skills, and hot technologies matched through the same postings. "
        "Posting-to-occupation links are model-inferred staging data, not reviewed requirements."
    ),
)
async def get_role_evidence_graph(
    occupation_id: str,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    source: Annotated[RoleEvidenceGraphSource, Depends(require_role_evidence_graph)],
) -> RoleEvidenceGraph:
    try:
        graph = await source.graph(occupation_id)
    except (Neo4jRepositoryError, Neo4jQueryApiError) as error:
        raise HTTPException(status_code=503) from error
    if graph is None:
        raise HTTPException(status_code=404)
    return graph

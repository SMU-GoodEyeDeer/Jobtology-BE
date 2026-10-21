from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from jobtology_be.api.errors import ErrorResponse
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.live_source import _allowed_query, require_live_source_feed
from jobtology_be.api.live_source_models import (
    ContextOccupation,
    OccupationContextResponse,
    RoleContextSelection,
)
from jobtology_be.corpus.neo4j_client import Neo4jQueryApiError
from jobtology_be.corpus.neo4j_repository import Neo4jRepositoryError
from jobtology_be.corpus.role_evidence_graph import RoleEvidenceGraph
from jobtology_be.infrastructure.persistence.live_source_feed import (
    LiveSourceFeed,
    LiveSourceReadError,
)
from jobtology_be.product_roles.models import ProductRolePolicy
from jobtology_be.product_roles.posting_context import posting_context_policy

router = APIRouter(tags=["native catalog"])


class RoleEvidenceGraphSource(Protocol):
    async def graph(self, occupation_id: str) -> RoleEvidenceGraph | None: ...


async def require_role_evidence_graph() -> RoleEvidenceGraphSource:
    raise HTTPException(status_code=503)


@lru_cache(maxsize=1)
def _role_policy() -> ProductRolePolicy:
    resource = files("jobtology_be.product_roles").joinpath("policy.v1.json")
    if not resource.is_file():
        resource = Path(__file__).resolve().parents[3] / "config/product_roles/policy.v1.json"
    return ProductRolePolicy.model_validate_json(resource.read_text())


@router.get("/roles/{occupation_id}/posting-context", response_model=OccupationContextResponse,
            responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                       422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
            summary="Read separately labelled inferred posting context for a product role")
async def role_posting_context(
    occupation_id: str, request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    feed: Annotated[LiveSourceFeed, Depends(require_live_source_feed)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OccupationContextResponse:
    """Role-level exposure is not evidence that any competency is required or satisfied."""
    _allowed_query(request, frozenset({"limit", "offset"}))
    role = next((role for role in _role_policy().roles if role.occupation_id == occupation_id), None)
    if role is None:
        raise HTTPException(status_code=404)
    # O*NET filters broaden context discovery; they establish no NCS equivalence
    # and never enter the approved competency artifact or analysis inputs.
    context_policy = posting_context_policy()
    codes = sorted({*role.occupation_codes, *(code[:8] for code in role.additional_base_codes),
                    *context_policy.roles.get(occupation_id, ())})
    try:
        result = await feed.occupation_context(codes, limit, offset)
        return result.model_copy(update={"role_context": RoleContextSelection(
            role_id=occupation_id, version=context_policy.version, basis=context_policy.basis,
            onet_occupations=[ContextOccupation(code=code, name=context_policy.occupations[code].title)
                              for code in context_policy.roles.get(occupation_id, ())],
        )})
    except LiveSourceReadError as error:
        raise HTTPException(status_code=error.status_code) from None


@router.get(
    "/roles/{occupation_id}/evidence-graph",
    response_model=RoleEvidenceGraph,
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        404: {"model": ErrorResponse, "description": "The product role is unknown."},
        503: {"model": ErrorResponse, "description": "The native Neo4j graph is unavailable."},
    },
    summary="Read a product role's bounded evidence graph and discovery context",
    description=(
        "Returns a bounded graph for one product role: its mapped NCS occupations, "
        "model-inferred posting counts, NCS competency units, and the O*NET occupations, "
        "essential skills, and hot technologies matched through the same postings. "
        "Independent role-level O*NET reference branches use curated discovery context, "
        "not NCS equivalence. Their current posting-association counts use the same "
        "catalog feed as the posting UI. Missing counts are disclosed in warnings. "
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

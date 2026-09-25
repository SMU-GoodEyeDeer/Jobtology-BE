from typing import Annotated, ClassVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, JsonValue

from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.application.m5_queries import (
    DashboardView,
    M5DataUnavailableError,
    M5Queries,
    OccupationView,
    RouteProposalView,
    TraceView,
)

router = APIRouter(tags=["m5-queries"])


class OccupationResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    occupation_id: str
    basis_version: str
    release_id: str


class RouteProposalResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    proposal_id: UUID
    analysis_id: UUID
    decision_trace_id: UUID
    proposal_hash: str
    profile_version: int
    feasibility: str
    optimization_status: str
    constraints_snapshot: dict[str, JsonValue]
    steps: tuple[dict[str, JsonValue], ...]
    basis_version: str
    release_id: str | None
    methodology_version: str


class TraceResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    trace_id: UUID
    kind: str
    input_hash: str
    versions: dict[str, JsonValue]
    release_id: str | None
    outputs: dict[str, JsonValue]


class DashboardResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    state: str
    goal_id: UUID | None
    analysis_id: UUID | None
    active_roadmap_id: UUID | None
    recompute_request_id: UUID | None
    recompute_state: str | None
    next_actions: tuple[dict[str, JsonValue], ...]
    attention_items: tuple[str, ...]
    recent_event_kinds: tuple[str, ...]


async def require_m5_queries() -> M5Queries:
    raise HTTPException(status_code=503)


@router.get(
    "/occupations",
    response_model=tuple[OccupationResponse, ...],
    summary="List published occupations",
    description="Lists occupations from the configured published corpus snapshot.",
)
async def get_occupations(
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    queries: Annotated[M5Queries, Depends(require_m5_queries)],
) -> tuple[OccupationResponse, ...]:
    try:
        occupations = await queries.get_occupations()
    except M5DataUnavailableError as error:
        raise HTTPException(status_code=503) from error
    return tuple(_occupation_response(item) for item in occupations)


@router.get(
    "/route-proposals/{proposal_id}",
    response_model=RouteProposalResponse,
    summary="Get route proposal",
    description="Returns one route proposal visible to the authenticated user.",
)
async def get_route_proposal(
    proposal_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    queries: Annotated[M5Queries, Depends(require_m5_queries)],
) -> RouteProposalResponse:
    return _route_proposal_response(await queries.get_route_proposal(principal.user_id, proposal_id))


@router.get(
    "/traces/{trace_id}",
    response_model=TraceResponse,
    summary="Get decision trace",
    description="Returns the recorded trace for a visible analysis or route proposal decision.",
)
async def get_trace(
    trace_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    queries: Annotated[M5Queries, Depends(require_m5_queries)],
) -> TraceResponse:
    return _trace_response(await queries.get_trace(principal.user_id, trace_id))


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="Get dashboard state",
    description="Returns next actions and current state, optionally scoped to one goal.",
)
async def get_dashboard(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    queries: Annotated[M5Queries, Depends(require_m5_queries)],
    goal_id: UUID | None = None,
) -> DashboardResponse:
    return _dashboard_response(await queries.get_dashboard(principal.user_id, goal_id))


def _occupation_response(value: OccupationView) -> OccupationResponse:
    return OccupationResponse(
        occupation_id=value.occupation_id,
        basis_version=value.basis_version,
        release_id=value.release_id,
    )


def _route_proposal_response(value: RouteProposalView) -> RouteProposalResponse:
    return RouteProposalResponse(
        proposal_id=value.proposal_id,
        analysis_id=value.analysis_id,
        decision_trace_id=value.decision_trace_id,
        proposal_hash=value.proposal_hash,
        profile_version=value.profile_version,
        feasibility=value.feasibility,
        optimization_status=value.optimization_status,
        constraints_snapshot=dict(value.constraints_snapshot),
        steps=tuple(dict(step) for step in value.steps),
        basis_version=value.basis_version,
        release_id=value.release_id,
        methodology_version=value.methodology_version,
    )


def _trace_response(value: TraceView) -> TraceResponse:
    return TraceResponse(
        trace_id=value.trace_id,
        kind=value.kind,
        input_hash=value.input_hash,
        versions=dict(value.versions),
        release_id=value.release_id,
        outputs=dict(value.outputs),
    )


def _dashboard_response(value: DashboardView) -> DashboardResponse:
    return DashboardResponse(
        state=value.state,
        goal_id=value.goal_id,
        analysis_id=None if value.analysis is None else value.analysis.analysis_id,
        active_roadmap_id=None if value.active_roadmap is None else value.active_roadmap.roadmap_id,
        recompute_request_id=None if value.recompute is None else value.recompute.recompute_request_id,
        recompute_state=None if value.recompute is None else value.recompute.state,
        next_actions=tuple(
            {
                "kind": item.kind,
                "step_id": str(item.step_id),
                "title": item.title,
                "reason_code": item.reason_code,
                "due_at": None if item.due_at is None else item.due_at.isoformat(),
            }
            for item in value.next_actions
        ),
        attention_items=value.attention_items,
        recent_event_kinds=tuple(item.kind for item in value.recent_events),
    )

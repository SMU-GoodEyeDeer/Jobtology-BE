from typing import Annotated, assert_never
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from starlette.responses import JSONResponse

from jobtology_be.api.errors import ErrorResponse
from jobtology_be.api.idempotency import (
    IdempotencyReplay,
    IdempotencyRequest,
    IdempotencyStore,
    execute_if_requested,
    require_idempotency_store,
)
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.product_queries import require_product_queries
from jobtology_be.api.roadmap_models import (
    RoadmapCreateRequest,
    RoadmapDetailResponse,
    RoadmapListResponse,
    RoadmapMutationRequest,
    RoadmapResponse,
    RoadmapStepResponse,
    StepStateRequest,
)
from jobtology_be.application.queries import ProductQueries, RoadmapView
from jobtology_be.application.services.roadmaps import (
    RoadmapCreateCommand,
    RoadmapMutationCommand,
    RoadmapResult,
    RoadmapService,
    StepStateCommand,
)

router = APIRouter(tags=["roadmaps"])


async def require_roadmap_service() -> RoadmapService:
    raise HTTPException(status_code=503)


def _response(result: RoadmapResult) -> RoadmapResponse:
    return RoadmapResponse(
        roadmap_id=result.roadmap_id,
        roadmap_version=result.roadmap_version,
        state=result.state,
    )


def _detail_response(roadmap: RoadmapView) -> RoadmapDetailResponse:
    return RoadmapDetailResponse(
        roadmap_id=roadmap.roadmap_id,
        roadmap_version=roadmap.roadmap_version,
        state=roadmap.state,
        goal_id=roadmap.goal_id,
        proposal_id=roadmap.proposal_id,
        title=roadmap.title,
        profile_version=roadmap.profile_version,
        release_id=roadmap.release_id,
        validity=roadmap.validity,
        steps=tuple(
            RoadmapStepResponse(
                step_id=step.step_id,
                step_key=step.step_key,
                position=step.position,
                action_id=step.action_id,
                template_revision=step.template_revision,
                state=step.state,
                planned_start=step.planned_start,
                planned_end=step.planned_end,
                outcomes=step.outcomes,
                criteria=step.criteria,
                prerequisite_step_ids=step.prerequisite_step_ids,
            )
            for step in roadmap.steps
        ),
    )


async def _create_response(
    user_id: UUID,
    request: RoadmapCreateRequest,
    roadmap_service: RoadmapService,
) -> RoadmapResponse:
    return _response(
        await roadmap_service.create(
            user_id,
            RoadmapCreateCommand(
                expected_profile_version=request.expected_profile_version,
                goal_id=request.goal_id,
                proposal_id=request.proposal_id,
                title=request.title,
            ),
        )
    )


@router.get(
    "/roadmaps",
    response_model=RoadmapListResponse,
    summary="List saved roadmaps",
    description="Returns roadmaps visible to the authenticated user.",
    responses={401: {"model": ErrorResponse, "description": "Authentication is required."}},
)
async def list_roadmaps(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    product_queries: Annotated[ProductQueries, Depends(require_product_queries)],
) -> RoadmapListResponse:
    return RoadmapListResponse(
        items=tuple(_detail_response(roadmap) for roadmap in await product_queries.list_roadmaps(principal.user_id))
    )


@router.get(
    "/roadmaps/{roadmap_id}",
    response_model=RoadmapDetailResponse,
    summary="Get saved roadmap",
    description="Returns a roadmap and its current step states.",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {"model": ErrorResponse, "description": "The request is not permitted."},
        404: {"model": ErrorResponse, "description": "The roadmap was not found."},
    },
)
async def get_roadmap(
    roadmap_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    product_queries: Annotated[ProductQueries, Depends(require_product_queries)],
) -> RoadmapDetailResponse:
    return _detail_response(await product_queries.get_roadmap(principal.user_id, roadmap_id))


@router.post(
    "/roadmaps",
    response_model=RoadmapResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft roadmap",
    description=(
        "Creates a `DRAFT` roadmap from a goal and route proposal. Activate it explicitly with the "
        "roadmap mutation endpoint. An `Idempotency-Key` replays the first successful response for "
        "the same payload for 24 hours."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {"model": ErrorResponse, "description": "The request is not permitted."},
        409: {"model": ErrorResponse, "description": "Version or idempotency conflict."},
        503: {"model": ErrorResponse, "description": "The roadmap service is unavailable."},
    },
)
async def create_roadmap(
    request: RoadmapCreateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    roadmap_service: Annotated[RoadmapService, Depends(require_roadmap_service)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            description="Optional 1-255 character key for 24-hour same-payload response replay.",
        )
    ] = None,
    idempotency_store: Annotated[IdempotencyStore | None, Depends(require_idempotency_store)] = None,
) -> RoadmapResponse | JSONResponse:
    async def operation() -> RoadmapResponse:
        return await _create_response(principal.user_id, request, roadmap_service)

    idempotency_result: RoadmapResponse | IdempotencyReplay = await execute_if_requested(
        idempotency_store,
        None
        if idempotency_key is None
        else IdempotencyRequest(
            user_id=principal.user_id,
            method="POST",
            path="/api/v1/roadmaps",
            key=idempotency_key,
            payload=request,
        ),
        status.HTTP_201_CREATED,
        operation,
    )
    match idempotency_result:
        case IdempotencyReplay(response_status=response_status, response=response):
            return JSONResponse(status_code=response_status, content=dict(response))
        case RoadmapResponse() as response:
            return response
        case unreachable:
            assert_never(unreachable)


@router.patch(
    "/roadmaps/{roadmap_id}",
    response_model=RoadmapResponse,
    summary="Mutate saved roadmap",
    description="Use `ACTIVATE`, `ARCHIVE`, or `RENAME` with current roadmap and profile versions.",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {"model": ErrorResponse, "description": "The request is not permitted."},
        404: {"model": ErrorResponse, "description": "The roadmap was not found."},
        409: {"model": ErrorResponse, "description": "The submitted version is stale."},
        503: {"model": ErrorResponse, "description": "The roadmap service is unavailable."},
    },
)
async def mutate_roadmap(
    roadmap_id: UUID,
    request: RoadmapMutationRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    roadmap_service: Annotated[RoadmapService, Depends(require_roadmap_service)],
    product_queries: Annotated[ProductQueries, Depends(require_product_queries)],
) -> RoadmapResponse:
    match request.operation:
        case "ACTIVATE":
            state = "ACTIVE"
        case "ARCHIVE":
            state = "ARCHIVED"
        case "RENAME":
            state = (await product_queries.get_roadmap(principal.user_id, roadmap_id)).state
        case unreachable:
            assert_never(unreachable)
    return _response(
        await roadmap_service.mutate(
            principal.user_id,
            RoadmapMutationCommand(
                roadmap_id=roadmap_id,
                expected_roadmap_version=request.expected_roadmap_version,
                expected_profile_version=request.expected_profile_version,
                state=state,
                title=request.title,
            ),
        )
    )


@router.patch(
    "/roadmaps/{roadmap_id}/steps/{step_id}",
    response_model=RoadmapResponse,
    summary="Update roadmap step state",
    description="Changes one step to `TODO`, `IN_PROGRESS`, or `COMPLETED` using current versions.",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {"model": ErrorResponse, "description": "The request is not permitted."},
        404: {"model": ErrorResponse, "description": "The roadmap or step was not found."},
        409: {"model": ErrorResponse, "description": "The submitted version is stale."},
        503: {"model": ErrorResponse, "description": "The roadmap service is unavailable."},
    },
)
async def update_step_state(
    roadmap_id: UUID,
    step_id: UUID,
    request: StepStateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    roadmap_service: Annotated[RoadmapService, Depends(require_roadmap_service)],
) -> RoadmapResponse:
    return _response(
        await roadmap_service.update_step(
            principal.user_id,
            StepStateCommand(
                roadmap_id=roadmap_id,
                step_id=step_id,
                expected_roadmap_version=request.expected_roadmap_version,
                expected_profile_version=request.expected_profile_version,
                state=request.state,
            ),
        )
    )

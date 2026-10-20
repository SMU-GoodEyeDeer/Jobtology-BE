from collections.abc import Mapping
from typing import Annotated, Final, assert_never
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, JsonValue, TypeAdapter
from starlette.responses import JSONResponse

from jobtology_be.api.analyses import AnalysisSkillResponse, require_requirement_metadata
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
    RoadmapRevisionAppliedResponse,
    RoadmapRevisionRequest,
    RoadmapRevisionResponse,
    RoadmapStepResponse,
    StepStateRequest,
)
from jobtology_be.application.queries import ProductQueries, RoadmapView
from jobtology_be.application.requirement_metadata import (
    RequirementMetadataLookup,
    build_analysis_summary,
)
from jobtology_be.application.roadmap_revisions import RoadmapRevisionApply
from jobtology_be.application.services.roadmaps import (
    RoadmapCreateCommand,
    RoadmapMutationCommand,
    RoadmapResult,
    RoadmapService,
    RoadmapState,
    StepState,
    StepStateCommand,
)

router = APIRouter(tags=["roadmaps"])
_ROADMAP_STATE: Final = TypeAdapter[RoadmapState](RoadmapState)
_STEP_STATE: Final = TypeAdapter[StepState](StepState)
_JSON_OBJECT: Final = TypeAdapter(dict[str, JsonValue])
_JSON_ARRAY: Final = TypeAdapter(tuple[JsonValue, ...])


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
        analysis_id=roadmap.analysis_id,
        roadmap_id=roadmap.roadmap_id,
        roadmap_version=roadmap.roadmap_version,
        state=_ROADMAP_STATE.validate_python(roadmap.state),
        goal_id=roadmap.goal_id,
        proposal_id=roadmap.proposal_id,
        title=roadmap.title,
        profile_version=roadmap.profile_version,
        release_id=roadmap.release_id,
        validity=_JSON_OBJECT.validate_python(roadmap.validity),
        steps=tuple(
            RoadmapStepResponse(
                step_id=step.step_id,
                step_key=step.step_key,
                position=step.position,
                action_id=step.action_id,
                template_revision=step.template_revision,
                state=_STEP_STATE.validate_python(step.state),
                planned_start=step.planned_start,
                planned_end=step.planned_end,
                outcomes=_JSON_ARRAY.validate_python(step.outcomes),
                criteria=_JSON_ARRAY.validate_python(step.criteria),
                prerequisite_step_ids=step.prerequisite_step_ids,
                title=step.title,
                description=step.criteria[0] if step.criteria and isinstance(step.criteria[0], str) else None,
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
            state = _ROADMAP_STATE.validate_python(
                (await product_queries.get_roadmap(principal.user_id, roadmap_id)).state
            )
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


@router.get('/roadmaps/{roadmap_id}/revision', response_model=RoadmapRevisionResponse,
            summary='Compare an active roadmap with a revised proposal')
async def preview_roadmap_revision(
    roadmap_id: UUID, proposal_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[RoadmapService, Depends(require_roadmap_service)],
) -> RoadmapRevisionResponse:
    return RoadmapRevisionResponse.model_validate(
        await service.preview_revision(principal.user_id, roadmap_id, proposal_id), from_attributes=True,
    )


@router.post('/roadmaps/{roadmap_id}/revision', response_model=RoadmapRevisionAppliedResponse,
             summary='Apply a reviewed roadmap revision atomically',
             description='Requires the exact reviewed proposal hash and current profile, roadmap and weekly plan versions. '
                         'The old active roadmap remains unchanged until the revised roadmap is fully ready to activate. '
                         'Completed history and original completion lineage are preserved without creating new credit.',
             responses={409: {'model': ErrorResponse, 'description': 'The comparison is stale or cannot preserve completed history.'}})
async def apply_roadmap_revision(
    roadmap_id: UUID, request: RoadmapRevisionRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[RoadmapService, Depends(require_roadmap_service)],
) -> RoadmapRevisionAppliedResponse:
    result = await service.apply_revision(principal.user_id, RoadmapRevisionApply(
        user_id=principal.user_id, roadmap_id=roadmap_id, proposal_id=request.proposal_id,
        expected_proposal_hash=request.expected_proposal_hash,
        expected_roadmap_version=request.expected_roadmap_version,
        expected_profile_version=request.expected_profile_version,
        expected_plan_version=request.expected_plan_version, title=request.title,
    ))
    return RoadmapRevisionAppliedResponse(
        roadmap_id=result.roadmap_id, roadmap_version=result.roadmap_version, state=result.state,
        previous_roadmap_id=result.previous_roadmap_id, preserved_completed_count=result.preserved_completed_count,
    )


class RoadmapSkillResponse(AnalysisSkillResponse):
    step_ids: tuple[UUID, ...] = ()
    source_analysis_id: UUID


class RoadmapSkillsResponse(BaseModel):
    roadmap_id: UUID
    analysis_id: UUID
    release_id: str | None
    skills: tuple[RoadmapSkillResponse, ...]


@router.get('/roadmaps/{roadmap_id}/skills', response_model=RoadmapSkillsResponse,
            summary='Read full skill metadata from the roadmap origin analysis',
            description='Includes satisfied skills. Stored metadata is pinned; legacy metadata is supplemented only '
                        'from the exact same release. This endpoint never borrows the latest dashboard analysis.')
async def get_roadmap_skills(
    roadmap_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    queries: Annotated[ProductQueries, Depends(require_product_queries)],
    metadata: Annotated[RequirementMetadataLookup | None, Depends(require_requirement_metadata)],
) -> RoadmapSkillsResponse:
    roadmap = await queries.get_roadmap(principal.user_id, roadmap_id)
    if roadmap.analysis_id is None:
        raise HTTPException(status_code=503, detail='Roadmap origin analysis is unavailable')
    analysis = await queries.get_analysis(principal.user_id, roadmap.analysis_id)
    lookup = metadata if analysis.release_id is not None and getattr(metadata, 'release_id', None) == analysis.release_id else None
    summary = build_analysis_summary(analysis.results, lookup, include_satisfied=True)
    if summary is None:
        raise HTTPException(status_code=503, detail='Roadmap origin metadata is unavailable')
    history = roadmap.validity.get('revision_history_steps', {})
    history = history if isinstance(history, Mapping) else {}
    skills = [RoadmapSkillResponse(
        **AnalysisSkillResponse.model_validate(item, from_attributes=True).model_dump(),
        source_analysis_id=analysis.analysis_id,
        step_ids=tuple(step.step_id for step in roadmap.steps if step.step_key not in history
                       and any(isinstance(outcome, Mapping) and outcome.get('requirement_key') == item.requirement_key
                               for outcome in step.outcomes)),
    ) for item in summary.skills]
    historical: dict[tuple[UUID, str], RoadmapSkillResponse] = {}
    source_cache = {}
    for step in roadmap.steps:
        document = history.get(step.step_key)
        if not isinstance(document, Mapping):
            continue
        source_id = UUID(str(document['source_analysis_id']))
        requirements = document.get('source_requirements')
        source_release = document.get('source_release_id')
        if not isinstance(requirements, (list, tuple)) or not requirements:
            if source_id not in source_cache:
                source_cache[source_id] = await queries.get_analysis(principal.user_id, source_id)
            source = source_cache[source_id]
            source_results, source_release = source.results, source.release_id
        else:
            source_results = {'requirements': requirements}
        source_lookup = metadata if source_release is not None and getattr(metadata, 'release_id', None) == source_release else None
        source_summary = build_analysis_summary(source_results, source_lookup, include_satisfied=True)
        if source_summary is None:
            continue
        keys = {outcome.get('requirement_key') for outcome in step.outcomes if isinstance(outcome, Mapping)}
        for item in source_summary.skills:
            if item.requirement_key not in keys:
                continue
            identity = (source_id, item.requirement_key)
            prior_ids = historical[identity].step_ids if identity in historical else ()
            details = AnalysisSkillResponse.model_validate(item, from_attributes=True).model_dump()
            details.update(status='SATISFIED', credit=1.0, estimated_hours=0, achievement='완료한 역량')
            historical[identity] = RoadmapSkillResponse(**details, source_analysis_id=source_id,
                                                      step_ids=(*prior_ids, step.step_id))
    return RoadmapSkillsResponse(
        roadmap_id=roadmap_id, analysis_id=analysis.analysis_id, release_id=analysis.release_id,
        skills=(*skills, *historical.values()),
    )

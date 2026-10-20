from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, ClassVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from jobtology_be.api.analyses import require_requirement_metadata
from jobtology_be.api.errors import ErrorResponse
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.product_queries import require_product_queries
from jobtology_be.application.queries import ProductQueries
from jobtology_be.application.requirement_metadata import RequirementMetadataLookup
from jobtology_be.application.weekly_plan import WeeklyPlanSchedulingError, build_weekly_plan
from jobtology_be.application.weekly_plan_models import (
    PlanContext,
    PlanOverrides,
    PlanStepInput,
    WeeklyPlanResponse,
)
from jobtology_be.application.weekly_plan_store import WeeklyPlanStore

router = APIRouter(tags=['roadmaps'])


class WeeklyPlanRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True, strict=True)

    expected_version: int = Field(ge=0)
    weekly_hours: int = Field(ge=1, le=60)
    rest_weeks: list[Annotated[int, Field(ge=1, le=104)]]
    order: list[str]
    hours: dict[str, Annotated[int, Field(ge=1, le=400)]]

    @model_validator(mode='after')
    def unique_rest_weeks(self) -> 'WeeklyPlanRequest':
        if len(self.rest_weeks) != len(set(self.rest_weeks)):
            message = 'rest_weeks must be unique'
            raise ValueError(message)
        return self


async def require_weekly_plan_store() -> WeeklyPlanStore:
    raise HTTPException(status_code=503)


@dataclass(frozen=True, slots=True)
class _PlanInputs:
    user_id: UUID
    context: PlanContext
    steps: tuple[PlanStepInput, ...]
    store: WeeklyPlanStore


async def _load_plan(
    roadmap_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    queries: Annotated[ProductQueries, Depends(require_product_queries)],
    store: Annotated[WeeklyPlanStore, Depends(require_weekly_plan_store)],
    metadata: Annotated[RequirementMetadataLookup | None, Depends(require_requirement_metadata)],
) -> _PlanInputs:
    roadmap = await queries.get_roadmap(principal.user_id, roadmap_id)
    context = await store.get_context(principal.user_id, roadmap_id)
    steps: list[PlanStepInput] = []
    for step in roadmap.steps:
        outcome = step.outcomes[0] if step.outcomes else None
        key = outcome.get('requirement_key') if isinstance(outcome, Mapping) else None
        pinned = (context.step_metadata[step.step_key] if step.step_key in context.step_metadata else
                  context.requirement_metadata.get(key) if isinstance(key, str) else None)
        if (step.step_key not in context.step_metadata and isinstance(key, str) and key not in context.requirement_metadata
                and roadmap.release_id is not None and getattr(metadata, 'release_id', None) == roadmap.release_id):
            pinned = metadata.lookup(key) if metadata is not None else None
        steps.append(PlanStepInput(step=step, metadata=pinned))
    return _PlanInputs(principal.user_id, context, tuple(steps), store)


_Inputs = Annotated[_PlanInputs, Depends(_load_plan)]
_ERRORS: dict[int | str, dict[str, type[BaseModel] | str]] = {
    401: {'model': ErrorResponse, 'description': 'Authentication is required.'},
    403: {'model': ErrorResponse, 'description': 'The request is not permitted.'},
    404: {'model': ErrorResponse, 'description': 'The roadmap was not found.'},
    409: {'model': ErrorResponse, 'description': 'The weekly plan version changed. Reload before saving.'},
    422: {'model': ErrorResponse, 'description': 'The submitted plan is invalid.'},
}


@router.get('/roadmaps/{roadmap_id}/plan', response_model=WeeklyPlanResponse,
            summary='Get weekly roadmap plan', responses=_ERRORS)
async def get_weekly_plan(inputs: _Inputs) -> WeeklyPlanResponse:
    overrides = await inputs.store.get(inputs.user_id, inputs.context.roadmap_id)
    return _build_plan(inputs.context, inputs.steps, overrides)


@router.put('/roadmaps/{roadmap_id}/plan', response_model=WeeklyPlanResponse,
            summary='Save weekly roadmap plan overrides', responses=_ERRORS)
async def put_weekly_plan(request: WeeklyPlanRequest, inputs: _Inputs) -> WeeklyPlanResponse:
    existing = await inputs.store.get(inputs.user_id, inputs.context.roadmap_id)
    plan = _overrides(request, inputs, saved_at=datetime.now(UTC), existing=existing)
    preview = _build_plan(inputs.context, inputs.steps, plan)
    if not preview.is_customized:
        plan = plan.model_copy(update={'is_customized': False})
    plan = plan.model_copy(update={'step_schedules': {str(step.step_id): step for step in preview.steps}})
    overrides = await inputs.store.upsert(inputs.user_id, inputs.context.roadmap_id, plan)
    return _build_plan(inputs.context, inputs.steps, overrides)


def _overrides(
    request: WeeklyPlanRequest, inputs: _PlanInputs, *, saved_at: datetime | None,
    existing: PlanOverrides | None = None,
) -> PlanOverrides:
    step_ids = {str(item.step.step_id) for item in inputs.steps}
    if len(request.order) != len(step_ids) or set(request.order) != step_ids:
        raise HTTPException(status_code=422, detail='order must be an exact permutation of step IDs')
    if not set(request.hours) <= step_ids:
        raise HTTPException(status_code=422, detail='hours contains unknown step IDs')
    recommended = _build_plan(inputs.context, inputs.steps)
    current = _build_plan(inputs.context, inputs.steps, existing)
    completed = {str(step.step_id): step for step in current.steps if step.state == 'COMPLETED'}
    if any(key in request.hours and request.hours[key] != step.hours for key, step in completed.items()):
        raise HTTPException(status_code=422, detail='completed step hours cannot be changed')
    hours = {str(step.step_id): request.hours[str(step.step_id)]
             for step in recommended.steps if str(step.step_id) in request.hours
             and request.hours[str(step.step_id)] != step.recommended_hours}
    return PlanOverrides(
        weekly_hours=request.weekly_hours, rest_weeks=tuple(sorted(request.rest_weeks)),
        order=tuple(request.order), hours=hours, saved_at=saved_at,
        version=request.expected_version,
        step_schedules={key: step for key, step in completed.items()},
    )


@router.post('/roadmaps/{roadmap_id}/plan/preview', response_model=WeeklyPlanResponse,
             summary='Preview a weekly plan without saving; null previews the solver schedule',
             responses=_ERRORS)
async def preview_weekly_plan(inputs: _Inputs, request: WeeklyPlanRequest | None = None) -> WeeklyPlanResponse:
    existing = await inputs.store.get(inputs.user_id, inputs.context.roadmap_id)
    overrides = (_overrides(request, inputs, saved_at=None, existing=existing)
                 if request is not None else existing.model_copy(update={'is_customized': False}) if existing else None)
    return _build_plan(inputs.context, inputs.steps, overrides)


@router.delete('/roadmaps/{roadmap_id}/plan', response_model=WeeklyPlanResponse,
               summary='Reset weekly roadmap plan to recommendations', responses=_ERRORS)
async def delete_weekly_plan(inputs: _Inputs, expected_version: Annotated[int, Query(ge=0)]) -> WeeklyPlanResponse:
    reset = await inputs.store.delete(inputs.user_id, inputs.context.roadmap_id, expected_version)
    return _build_plan(inputs.context, inputs.steps, reset)


def _build_plan(context: PlanContext, steps: tuple[PlanStepInput, ...], overrides: PlanOverrides | None = None) -> WeeklyPlanResponse:
    try:
        return build_weekly_plan(context, steps, overrides)
    except WeeklyPlanSchedulingError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

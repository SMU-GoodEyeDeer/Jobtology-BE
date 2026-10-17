from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, ClassVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from jobtology_be.api.analyses import require_requirement_metadata
from jobtology_be.api.errors import ErrorResponse
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.product_queries import require_product_queries
from jobtology_be.application.queries import ProductQueries
from jobtology_be.application.requirement_metadata import RequirementMetadataLookup
from jobtology_be.application.weekly_plan import build_weekly_plan
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
        steps.append(PlanStepInput(step=step, metadata=metadata.lookup(key)
                                   if metadata is not None and isinstance(key, str) else None))
    return _PlanInputs(principal.user_id, context, tuple(steps), store)


_Inputs = Annotated[_PlanInputs, Depends(_load_plan)]
_ERRORS: dict[int | str, dict[str, type[BaseModel] | str]] = {
    401: {'model': ErrorResponse, 'description': 'Authentication is required.'},
    403: {'model': ErrorResponse, 'description': 'The request is not permitted.'},
    404: {'model': ErrorResponse, 'description': 'The roadmap was not found.'},
    422: {'model': ErrorResponse, 'description': 'The submitted plan is invalid.'},
}


@router.get('/roadmaps/{roadmap_id}/plan', response_model=WeeklyPlanResponse,
            summary='Get weekly roadmap plan', responses=_ERRORS)
async def get_weekly_plan(inputs: _Inputs) -> WeeklyPlanResponse:
    overrides = await inputs.store.get(inputs.user_id, inputs.context.roadmap_id)
    return build_weekly_plan(inputs.context, inputs.steps, overrides)


@router.put('/roadmaps/{roadmap_id}/plan', response_model=WeeklyPlanResponse,
            summary='Save weekly roadmap plan overrides', responses=_ERRORS)
async def put_weekly_plan(request: WeeklyPlanRequest, inputs: _Inputs) -> WeeklyPlanResponse:
    step_ids = {str(item.step.step_id) for item in inputs.steps}
    if len(request.order) != len(step_ids) or set(request.order) != step_ids:
        raise HTTPException(status_code=422, detail='order must be an exact permutation of step IDs')
    if not set(request.hours) <= step_ids:
        raise HTTPException(status_code=422, detail='hours contains unknown step IDs')
    recommended = build_weekly_plan(inputs.context, inputs.steps)
    hours = {str(step.step_id): request.hours[str(step.step_id)]
             for step in recommended.steps if str(step.step_id) in request.hours
             and request.hours[str(step.step_id)] != step.recommended_hours}
    overrides = await inputs.store.upsert(inputs.user_id, inputs.context.roadmap_id, PlanOverrides(
        weekly_hours=request.weekly_hours, rest_weeks=tuple(sorted(request.rest_weeks)),
        order=tuple(request.order), hours=hours, saved_at=datetime.now(UTC),
    ))
    return build_weekly_plan(inputs.context, inputs.steps, overrides)


@router.delete('/roadmaps/{roadmap_id}/plan', response_model=WeeklyPlanResponse,
               summary='Reset weekly roadmap plan to recommendations', responses=_ERRORS)
async def delete_weekly_plan(inputs: _Inputs) -> WeeklyPlanResponse:
    await inputs.store.delete(inputs.user_id, inputs.context.roadmap_id)
    return build_weekly_plan(inputs.context, inputs.steps)

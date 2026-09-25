from typing import Annotated, ClassVar
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, JsonValue

from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.product_queries import require_product_queries
from jobtology_be.application.queries import ProductQueries, RoadmapDiffView

router = APIRouter(tags=["roadmaps"])


class RoadmapScheduleChangeResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    step_key: str
    old_planned_start: str | None
    old_planned_end: str | None
    new_planned_start: str | None
    new_planned_end: str | None


class RoadmapDiffResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    roadmap_id: UUID
    roadmap_version: int
    proposal_id: UUID
    proposal_hash: str
    retained_step_keys: tuple[str, ...]
    added_step_keys: tuple[str, ...]
    removed_step_keys: tuple[str, ...]
    reordered_step_keys: tuple[str, ...]
    schedule_changes: tuple[RoadmapScheduleChangeResponse, ...]
    constraint_changes: dict[str, tuple[JsonValue, JsonValue]]


def _response(result: RoadmapDiffView) -> RoadmapDiffResponse:
    return RoadmapDiffResponse(
        roadmap_id=result.roadmap_id,
        roadmap_version=result.roadmap_version,
        proposal_id=result.proposal_id,
        proposal_hash=result.proposal_hash,
        retained_step_keys=result.retained_step_keys,
        added_step_keys=result.added_step_keys,
        removed_step_keys=result.removed_step_keys,
        reordered_step_keys=result.reordered_step_keys,
        schedule_changes=tuple(
            RoadmapScheduleChangeResponse(
                step_key=change.step_key,
                old_planned_start=None
                if change.old_planned_start is None
                else change.old_planned_start.isoformat(),
                old_planned_end=None if change.old_planned_end is None else change.old_planned_end.isoformat(),
                new_planned_start=None
                if change.new_planned_start is None
                else change.new_planned_start.isoformat(),
                new_planned_end=None if change.new_planned_end is None else change.new_planned_end.isoformat(),
            )
            for change in result.schedule_changes
        ),
        constraint_changes={key: tuple(value) for key, value in result.constraint_changes.items()},
    )


@router.get(
    "/roadmaps/{roadmap_id}/diff",
    response_model=RoadmapDiffResponse,
    summary="Compare roadmap with proposal",
    description="Returns schedule and constraint differences between a saved roadmap and a proposal.",
)
async def get_roadmap_diff(
    roadmap_id: UUID,
    proposal_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    product_queries: Annotated[ProductQueries, Depends(require_product_queries)],
) -> RoadmapDiffResponse:
    return _response(await product_queries.get_roadmap_diff(principal.user_id, roadmap_id, proposal_id))

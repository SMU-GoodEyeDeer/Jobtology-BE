from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.application.m5_queries import DashboardNextActionView
from jobtology_be.contracts import StepState
from jobtology_be.infrastructure.persistence.m5_dashboard_models import ActiveRoadmapRow
from jobtology_be.infrastructure.persistence.roadmap_source_validity import (
    parse_source_validity,
    source_is_current,
)
from jobtology_be.infrastructure.persistence.schema import roadmap_steps, step_dependencies
from jobtology_be.modules.dashboard.next_actions import (
    NextActionRequest,
    RoadmapCandidate,
    RoadmapStepCandidate,
    select_next_actions,
)

NEXT_ACTION_RULE_VERSION: Final = "M5_NEXT_ACTIONS_V1"
NEXT_ACTION_URGENCY_WINDOW: Final = timedelta(days=7)


async def load_next_actions(
    session: AsyncSession, roadmap: ActiveRoadmapRow, reference_at: datetime
) -> tuple[DashboardNextActionView, ...]:
    source_validity = parse_source_validity(roadmap.validity)
    if not source_is_current(source_validity, reference_at):
        return ()
    step_rows = (
        (
            await session.execute(
                select(
                    roadmap_steps.c.id,
                    roadmap_steps.c.position,
                    roadmap_steps.c.action_id,
                    roadmap_steps.c.state,
                    roadmap_steps.c.planned_end,
                ).where(roadmap_steps.c.roadmap_id == roadmap.view.roadmap_id)
            )
        )
        .mappings()
        .all()
    )
    dependencies = (
        (
            await session.execute(
                select(step_dependencies.c.step_id, step_dependencies.c.prerequisite_step_id).where(
                    step_dependencies.c.roadmap_id == roadmap.view.roadmap_id
                )
            )
        )
        .mappings()
        .all()
    )
    prerequisites: dict[UUID, list[str]] = {}
    for dependency in dependencies:
        prerequisites.setdefault(dependency["step_id"], []).append(
            str(dependency["prerequisite_step_id"])
        )
    completed = frozenset(str(step["id"]) for step in step_rows if step["state"] == "COMPLETED")
    selection = select_next_actions(
        NextActionRequest(
            reference_at=reference_at,
            urgency_window=NEXT_ACTION_URGENCY_WINDOW,
            rule_version=NEXT_ACTION_RULE_VERSION,
            roadmap=RoadmapCandidate(
                roadmap_id=str(roadmap.view.roadmap_id),
                roadmap_version=roadmap.view.roadmap_version,
                is_active=True,
            ),
            completed_step_ids=completed,
            deadline_actions=(),
            roadmap_steps=tuple(
                RoadmapStepCandidate(
                    roadmap_id=str(roadmap.view.roadmap_id),
                    roadmap_version=roadmap.view.roadmap_version,
                    step_id=str(step["id"]),
                    title=step["action_id"],
                    state=StepState(step["state"]),
                    roadmap_position=step["position"],
                    due_at=step["planned_end"],
                    expires_at=source_validity.expires_at,
                    source_is_valid=source_validity.source_is_valid,
                    prerequisite_step_ids=tuple(prerequisites.get(step["id"], [])),
                    support_refs=(),
                )
                for step in step_rows
            ),
        )
    )
    return tuple(
        DashboardNextActionView(
            kind=item.kind,
            step_id=UUID(item.navigation_target_id),
            title=item.title,
            reason_code=item.reason_code,
            due_at=item.due_at,
        )
        for item in selection.recommendations
        if item.step_id is not None
    )

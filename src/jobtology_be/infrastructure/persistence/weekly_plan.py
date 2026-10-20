import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.application.requirement_metadata import RequirementMetadata
from jobtology_be.application.weekly_plan_models import PlanContext, PlanOverrides, WeeklyPlanStep
from jobtology_be.infrastructure.persistence.contracts import (
    MissingRecordError,
    PersistenceConflictError,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    UTC_NOW,
    analyses,
    goals,
    roadmap_plan_overrides,
    roadmap_steps,
    roadmaps,
    route_proposals,
)


class _Constraints(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    available_hours_per_week: int | None = Field(default=None, ge=1, le=60)


class _StoredOverride(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    weekly_hours: int
    rest_weeks: tuple[int, ...]
    step_order: tuple[str, ...]
    step_hours: dict[str, int]
    updated_at: datetime
    version: int
    is_customized: bool
    step_schedules: dict[str, JsonValue]

    def plan(self) -> PlanOverrides:
        return PlanOverrides(weekly_hours=self.weekly_hours, rest_weeks=self.rest_weeks,
                             order=self.step_order, hours=self.step_hours, saved_at=self.updated_at,
                             version=self.version, is_customized=self.is_customized,
                             step_schedules={key: WeeklyPlanStep.model_validate_json(json.dumps(value))
                                             for key, value in self.step_schedules.items()})


class _StoredContext(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    created_at: datetime
    constraints_snapshot: _Constraints
    timezone: str
    goal_target_by: datetime
    proposal_steps: tuple[object, ...]
    analysis_results: dict[str, JsonValue] | None


class PostgresWeeklyPlanStore:
    def __init__(self, database: Database) -> None:
        self._database: Database = database

    async def _require_owner(self, session: AsyncSession, user_id: UUID, roadmap_id: UUID) -> None:
        owner = await session.scalar(select(roadmaps.c.id).where(
            roadmaps.c.id == roadmap_id, roadmaps.c.user_id == user_id,
        ).with_for_update())
        if owner is None:
            raise MissingRecordError(resource='roadmap')

    async def get_context(self, user_id: UUID, roadmap_id: UUID) -> PlanContext:
        async with self._database.sessions() as session:
            row = (await session.execute(select(
                roadmaps.c.created_at, route_proposals.c.constraints_snapshot,
                goals.c.timezone, goals.c.target_by.label('goal_target_by'),
                route_proposals.c.steps.label('proposal_steps'),
                analyses.c.results.label('analysis_results'),
            ).select_from(roadmaps.join(route_proposals, roadmaps.c.proposal_id == route_proposals.c.id)
                          .join(goals, roadmaps.c.goal_id == goals.c.id)
                          .join(analyses, route_proposals.c.analysis_id == analyses.c.id))
                .where(roadmaps.c.id == roadmap_id, roadmaps.c.user_id == user_id,
                       route_proposals.c.user_id == user_id,
                       goals.c.user_id == user_id))).mappings().one_or_none()
        if row is None:
            raise MissingRecordError(resource='roadmap')
        stored = _StoredContext.model_validate(row)
        constraints = stored.constraints_snapshot
        timezone = ZoneInfo(stored.timezone)
        target_date = stored.goal_target_by.astimezone(timezone).date()
        step_hours: dict[str, int] = {}
        step_allocations: dict[str, tuple[datetime, ...]] = {}
        for step in stored.proposal_steps:
            if not isinstance(step, Mapping):
                continue
            key, hours = step.get('step_key'), step.get('estimated_hours')
            if (isinstance(key, str) and key.strip() and isinstance(hours, int)
                    and not isinstance(hours, bool) and hours > 0):
                step_hours[key] = hours
                allocations = step.get('slot_allocations')
                if isinstance(allocations, (list, tuple)) and len(allocations) == hours:
                    starts = _allocation_starts(allocations, hours)
                    if starts is not None:
                        step_allocations[key] = starts
        metadata: dict[str, RequirementMetadata | None] = {}
        requirements = (stored.analysis_results or {}).get('requirements', ())
        if isinstance(requirements, (list, tuple)):
            for requirement in requirements:
                if isinstance(requirement, dict) and isinstance(requirement.get('requirement_key'), str):
                    key = requirement['requirement_key']
                    if 'metadata' in requirement:
                        metadata[key] = (TypeAdapter(RequirementMetadata).validate_python(requirement['metadata'])
                                         if requirement['metadata'] is not None else None)
        return PlanContext(roadmap_id=roadmap_id, created_at=stored.created_at,
                           recommended_weekly_hours=constraints.available_hours_per_week or 10,
                           target_date=target_date, timezone=stored.timezone,
                           step_hours=step_hours, step_allocations=step_allocations,
                           requirement_metadata=metadata, as_of=datetime.now(UTC))

    async def get(self, user_id: UUID, roadmap_id: UUID) -> PlanOverrides | None:
        async with self._database.sessions.begin() as session:
            await self._require_owner(session, user_id, roadmap_id)
            row = (await session.execute(select(roadmap_plan_overrides).where(
                roadmap_plan_overrides.c.roadmap_id == roadmap_id,
            ))).mappings().one_or_none()
        return _StoredOverride.model_validate(row).plan() if row is not None else None

    async def _write(self, session: AsyncSession, roadmap_id: UUID, plan: PlanOverrides) -> PlanOverrides:
        current = await session.scalar(select(roadmap_plan_overrides.c.version).where(
            roadmap_plan_overrides.c.roadmap_id == roadmap_id,
        ))
        if (current or 0) != plan.version:
            raise PersistenceConflictError(resource='weekly plan')
        values = {'weekly_hours': plan.weekly_hours, 'rest_weeks': list(plan.rest_weeks),
                  'step_order': list(plan.order), 'step_hours': plan.hours,
                  'version': plan.version + 1, 'is_customized': plan.is_customized,
                  'step_schedules': {key: value.model_dump(mode='json')
                                     for key, value in plan.step_schedules.items()},
                  'updated_at': UTC_NOW}
        statement = insert(roadmap_plan_overrides).values(roadmap_id=roadmap_id, **values)
        row = (await session.execute(statement.on_conflict_do_update(
            index_elements=[roadmap_plan_overrides.c.roadmap_id], set_=values,
        ).returning(roadmap_plan_overrides))).mappings().one()
        return _StoredOverride.model_validate(row).plan()

    async def upsert(self, user_id: UUID, roadmap_id: UUID, plan: PlanOverrides) -> PlanOverrides:
        async with self._database.sessions.begin() as session:
            # Serialize with step/proposal writes using the existing owner row lock;
            # the override version remains independent of the roadmap version.
            await self._require_owner(session, user_id, roadmap_id)
            completed = (await session.execute(select(roadmap_steps.c.id).where(
                roadmap_steps.c.roadmap_id == roadmap_id, roadmap_steps.c.state == 'COMPLETED',
            ))).scalars().all()
            if any(str(key) in plan.step_schedules and plan.step_schedules[str(key)].state != 'COMPLETED'
                   for key in completed):
                raise PersistenceConflictError(resource='roadmap progress')
            return await self._write(session, roadmap_id, plan)

    async def delete(self, user_id: UUID, roadmap_id: UUID, expected_version: int) -> PlanOverrides:
        context = await self.get_context(user_id, roadmap_id)
        async with self._database.sessions.begin() as session:
            await self._require_owner(session, user_id, roadmap_id)
            row = (await session.execute(select(roadmap_plan_overrides).where(
                roadmap_plan_overrides.c.roadmap_id == roadmap_id,
            ))).mappings().one_or_none()
            current = _StoredOverride.model_validate(row).plan() if row is not None else None
            completed = set((await session.execute(select(roadmap_steps.c.id).where(
                roadmap_steps.c.roadmap_id == roadmap_id, roadmap_steps.c.state == 'COMPLETED',
            ))).scalars().all())
            # Keep only completed history. Future tasks return to solver dates, and
            # must not later inherit an obsolete customization when completed.
            schedules = {key: step for key, step in current.step_schedules.items()
                         if UUID(key) in completed} if current else {}
            # A reset is a versioned tombstone; deleting the row would allow a stale
            # tab with version=0 to overwrite a plan that was changed and reset.
            return await self._write(session, roadmap_id, PlanOverrides(
                weekly_hours=context.recommended_weekly_hours, rest_weeks=(), order=(), hours={},
                saved_at=None, version=expected_version, is_customized=False, step_schedules=schedules,
            ))


def _allocation_starts(allocations: list | tuple, hours: int) -> tuple[datetime, ...] | None:
    try:
        starts: list[datetime] = []
        for allocation in allocations:
            if not isinstance(allocation, Mapping):
                return None
            first, last = allocation.get('starts_at'), allocation.get('ends_at')
            if not isinstance(first, str) or not isinstance(last, str):
                return None
            start, end = datetime.fromisoformat(first), datetime.fromisoformat(last)
            if start.utcoffset() is None or end.utcoffset() is None or end - start != timedelta(hours=1):
                return None
            starts.append(start)
        return tuple(starts) if len(set(starts)) == len(starts) == hours else None
    except (ValueError, TypeError, OverflowError):
        # One corrupt legacy step must not prevent the owner opening the plan.
        return None

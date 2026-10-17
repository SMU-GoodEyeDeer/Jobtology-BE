from datetime import date, datetime
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.application.weekly_plan import SEOUL
from jobtology_be.application.weekly_plan_models import PlanContext, PlanOverrides
from jobtology_be.infrastructure.persistence.contracts import MissingRecordError
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    UTC_NOW,
    roadmap_plan_overrides,
    roadmaps,
    route_proposals,
)


class _Constraints(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    available_hours_per_week: int | None = Field(default=None, ge=1, le=60)
    target_by: datetime | date | None = None


class _StoredOverride(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    weekly_hours: int
    rest_weeks: tuple[int, ...]
    step_order: tuple[str, ...]
    step_hours: dict[str, int]
    updated_at: datetime

    def plan(self) -> PlanOverrides:
        return PlanOverrides(weekly_hours=self.weekly_hours, rest_weeks=self.rest_weeks,
                             order=self.step_order, hours=self.step_hours, saved_at=self.updated_at)


class _StoredContext(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    created_at: datetime
    constraints_snapshot: _Constraints


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
            ).select_from(roadmaps.join(route_proposals, roadmaps.c.proposal_id == route_proposals.c.id))
                .where(roadmaps.c.id == roadmap_id, roadmaps.c.user_id == user_id,
                       route_proposals.c.user_id == user_id))).mappings().one_or_none()
        if row is None:
            raise MissingRecordError(resource='roadmap')
        stored = _StoredContext.model_validate(row)
        constraints = stored.constraints_snapshot
        target = constraints.target_by
        target_date = ((target if target.tzinfo else target.replace(tzinfo=SEOUL))
                       .astimezone(SEOUL).date()) if isinstance(target, datetime) else target
        return PlanContext(roadmap_id=roadmap_id, created_at=stored.created_at,
                           recommended_weekly_hours=constraints.available_hours_per_week or 10,
                           target_date=target_date)

    async def get(self, user_id: UUID, roadmap_id: UUID) -> PlanOverrides | None:
        async with self._database.sessions.begin() as session:
            await self._require_owner(session, user_id, roadmap_id)
            row = (await session.execute(select(roadmap_plan_overrides).where(
                roadmap_plan_overrides.c.roadmap_id == roadmap_id,
            ))).mappings().one_or_none()
        return _StoredOverride.model_validate(row).plan() if row is not None else None

    async def upsert(self, user_id: UUID, roadmap_id: UUID, plan: PlanOverrides) -> PlanOverrides:
        values = {'weekly_hours': plan.weekly_hours, 'rest_weeks': list(plan.rest_weeks),
                  'step_order': list(plan.order), 'step_hours': plan.hours, 'updated_at': UTC_NOW}
        statement = insert(roadmap_plan_overrides).values(roadmap_id=roadmap_id, **values)
        async with self._database.sessions.begin() as session:
            await self._require_owner(session, user_id, roadmap_id)
            row = (await session.execute(statement.on_conflict_do_update(
                index_elements=[roadmap_plan_overrides.c.roadmap_id], set_=values,
            ).returning(roadmap_plan_overrides))).mappings().one()
        return _StoredOverride.model_validate(row).plan()

    async def delete(self, user_id: UUID, roadmap_id: UUID) -> None:
        async with self._database.sessions.begin() as session:
            await self._require_owner(session, user_id, roadmap_id)
            _ = await session.execute(delete(roadmap_plan_overrides).where(
                roadmap_plan_overrides.c.roadmap_id == roadmap_id,
            ))

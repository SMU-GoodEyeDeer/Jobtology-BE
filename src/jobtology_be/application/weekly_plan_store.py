from typing import Protocol
from uuid import UUID

from jobtology_be.application.weekly_plan_models import PlanContext, PlanOverrides


class WeeklyPlanStore(Protocol):
    async def get_context(self, user_id: UUID, roadmap_id: UUID) -> PlanContext: ...

    async def get(self, user_id: UUID, roadmap_id: UUID) -> PlanOverrides | None: ...

    async def upsert(self, user_id: UUID, roadmap_id: UUID, plan: PlanOverrides) -> PlanOverrides: ...

    async def delete(self, user_id: UUID, roadmap_id: UUID, expected_version: int) -> PlanOverrides: ...

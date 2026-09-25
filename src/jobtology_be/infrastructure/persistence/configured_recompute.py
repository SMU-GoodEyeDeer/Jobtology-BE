from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from jobtology_be.contracts import CapabilityInput
from jobtology_be.infrastructure.persistence.context_carryforward import (
    ContextCarryforwardError,
    ContextCarryforwardInput,
    carry_forward_recompute_context,
)
from jobtology_be.infrastructure.persistence.contracts import RecomputeRequestCreate
from jobtology_be.infrastructure.persistence.outbox import OutboxRepository
from jobtology_be.infrastructure.persistence.schema import (
    goals,
    recompute_contexts,
    recompute_requests,
    route_preferences,
    user_capabilities,
)
from jobtology_be.modules.analyses.editorial_models import InputCompleteness
from jobtology_be.planning.contracts import PlanningConstraints


class ConfiguredRecomputeRepository(OutboxRepository):
    async def enqueue_for_active_user(
        self, session, user_id: UUID, profile_version: int, event_id: UUID
    ) -> None:
        active_goals = (
            await session.execute(
                select(goals.c.id)
                .where(goals.c.user_id == user_id, goals.c.status == "ACTIVE")
                .order_by(goals.c.updated_at.desc(), goals.c.id)
                .limit(2)
            )
        ).scalars().all()
        if len(active_goals) != 1:
            await self._enqueue_recompute(session, user_id, profile_version, event_id)
            return
        await self.enqueue_for_goal(
            session, user_id, profile_version, event_id, active_goals[0]
        )

    async def enqueue_for_goal(
        self, session, user_id: UUID, profile_version: int, event_id: UUID, goal_id: UUID
    ) -> None:
        context = await session.scalar(
            select(recompute_contexts.c.payload)
            .join(
                recompute_requests,
                recompute_requests.c.id == recompute_contexts.c.request_id,
            )
            .where(
                recompute_requests.c.user_id == user_id,
                recompute_requests.c.state.in_(("PENDING", "READY")),
                recompute_requests.c.profile_version < profile_version,
                recompute_contexts.c.payload["goal_id"].astext == str(goal_id),
            )
            .order_by(recompute_requests.c.profile_version.desc(), recompute_requests.c.created_at.desc())
            .limit(1)
        )
        if context is None:
            await self._enqueue_recompute(session, user_id, profile_version, event_id)
            return
        goal = await session.execute(
            select(goals.c.status, goals.c.occupation_id, goals.c.target_by).where(
                goals.c.id == goal_id,
                goals.c.user_id == user_id,
            )
        )
        goal_row = goal.one_or_none()
        if (
            goal_row is None
            or goal_row.status != "ACTIVE"
            or goal_row.occupation_id is None
        ):
            await self._enqueue_recompute(session, user_id, profile_version, event_id)
            return
        try:
            capabilities, completeness = await self._active_capabilities(session, user_id)
            constraints = await self._current_constraints(session, user_id, goal_row.target_by)
            carried_context = carry_forward_recompute_context(
                context,
                ContextCarryforwardInput(
                    user_id=user_id,
                    goal_id=goal_id,
                    goal_occupation_id=goal_row.occupation_id,
                    profile_version=profile_version,
                    goal_target_by=goal_row.target_by,
                    capabilities=capabilities,
                    completeness=completeness,
                    now=datetime.now(UTC),
                    current_constraints=constraints,
                ),
            )
        except ContextCarryforwardError:
            await self._enqueue_recompute(session, user_id, profile_version, event_id)
            return
        await self._persist_recompute_request(
            session,
            RecomputeRequestCreate(
                user_id=user_id,
                profile_version=profile_version,
                trigger_event_id=event_id,
                dedupe_key=f"configured-context:{event_id}",
                payload={"source": "configured-context"},
                context=carried_context,
            ),
        )

    async def _active_capabilities(
        self, session, user_id: UUID
    ) -> tuple[tuple[CapabilityInput, ...], InputCompleteness]:
        rows = (
            await session.execute(
                select(
                    user_capabilities.c.raw_text,
                    user_capabilities.c.entity_id,
                    user_capabilities.c.details,
                )
                .where(
                    user_capabilities.c.user_id == user_id,
                    user_capabilities.c.lifecycle == "ACTIVE",
                )
                .order_by(user_capabilities.c.created_at, user_capabilities.c.id)
            )
        ).all()
        try:
            capabilities = tuple(
                CapabilityInput(
                    raw_text=row.raw_text,
                    entity_id=row.entity_id,
                    experience_codes=list(row.details.get("experience_codes", ())),
                )
                for row in rows
            )
            completeness = InputCompleteness(
                entities_complete=bool(capabilities)
                and all(capability.entity_id is not None for capability in capabilities),
                experience_complete_entity_ids=frozenset(
                    row.entity_id
                    for row in rows
                    if row.entity_id is not None and "experience_codes" in row.details
                ),
            )
            return capabilities, completeness
        except ValidationError as error:
            raise ContextCarryforwardError() from error

    async def _current_constraints(
        self, session, user_id: UUID, target_by: datetime
    ) -> PlanningConstraints | None:
        preference = await session.execute(
            select(
                route_preferences.c.available_hours_per_week,
                route_preferences.c.budget_mode,
                route_preferences.c.max_out_of_pocket_krw,
                route_preferences.c.fastest_path,
                route_preferences.c.needs_portfolio,
                route_preferences.c.career_switch,
            ).where(route_preferences.c.user_id == user_id)
        )
        row = preference.one_or_none()
        if row is None:
            return None
        return PlanningConstraints(
            target_by=target_by,
            available_hours_per_week=row.available_hours_per_week,
            budget_mode=row.budget_mode,
            max_out_of_pocket_krw=row.max_out_of_pocket_krw,
            fastest_path=row.fastest_path,
            needs_portfolio=row.needs_portfolio,
            career_switch=row.career_switch,
        )

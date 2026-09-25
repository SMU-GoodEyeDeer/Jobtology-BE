from collections.abc import Callable
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.application.m5_queries import (
    DashboardAnalysisView,
    DashboardEventView,
    DashboardRecomputeView,
    DashboardRoadmapView,
    DashboardView,
)
from jobtology_be.infrastructure.persistence.contracts import MissingRecordError
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.m5_dashboard_actions import load_next_actions
from jobtology_be.infrastructure.persistence.m5_dashboard_models import ActiveRoadmapRow, GoalRow
from jobtology_be.infrastructure.persistence.schema import (
    analyses,
    goals,
    profiles,
    recompute_contexts,
    recompute_requests,
    roadmaps,
    user_state_events,
)


class PostgresM5DashboardQueries:
    def __init__(self, database: Database, now: Callable[[], datetime]) -> None:
        self._database = database
        self._now = now

    async def get_dashboard(self, user_id: UUID, goal_id: UUID | None) -> DashboardView:
        async with self._database.sessions() as session:
            goal = await self._dashboard_goal(session, user_id, goal_id)
            if goal is None:
                return DashboardView("EMPTY", None, None, None, None, (), (), ())
            analysis = await self._analysis(session, user_id, goal.id)
            roadmap = await self._active_roadmap(session, user_id, goal.id)
            recompute = await self._recompute(session, user_id, goal.id)
            events = await self._events(session, user_id)
            next_actions = () if roadmap is None else await load_next_actions(session, roadmap, self._now())
        roadmap_view = None if roadmap is None else roadmap.view
        state, attention_items = _dashboard_state(recompute, roadmap_view)
        return DashboardView(
            state,
            goal.id,
            analysis,
            roadmap_view,
            recompute,
            next_actions,
            attention_items,
            events,
        )

    async def _dashboard_goal(
        self, session: AsyncSession, user_id: UUID, goal_id: UUID | None
    ) -> GoalRow | None:
        statement = select(goals.c.id).where(goals.c.user_id == user_id)
        if goal_id is not None:
            selected = await session.scalar(statement.where(goals.c.id == goal_id))
            if selected is None:
                raise MissingRecordError(resource="goal")
            return GoalRow(id=selected)
        selected = await session.scalar(
            statement.where(goals.c.status == "ACTIVE").order_by(goals.c.updated_at.desc()).limit(1)
        )
        return None if selected is None else GoalRow(id=selected)

    async def _analysis(
        self, session: AsyncSession, user_id: UUID, goal_id: UUID
    ) -> DashboardAnalysisView | None:
        row = (
            (
                await session.execute(
                    select(
                        analyses.c.id,
                        analyses.c.profile_version,
                        analyses.c.basis_version,
                        analyses.c.release_id,
                        analyses.c.methodology_version,
                        analyses.c.status,
                        analyses.c.generated_at,
                    )
                    .join(profiles, profiles.c.latest_analysis_id == analyses.c.id)
                    .where(
                        profiles.c.user_id == user_id,
                        analyses.c.user_id == user_id,
                        analyses.c.goal_id == goal_id,
                        analyses.c.profile_version == profiles.c.version,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return DashboardAnalysisView(
            analysis_id=row["id"],
            profile_version=row["profile_version"],
            basis_version=row["basis_version"],
            release_id=row["release_id"],
            methodology_version=row["methodology_version"],
            status=row["status"],
            generated_at=row["generated_at"],
        )

    async def _active_roadmap(
        self, session: AsyncSession, user_id: UUID, goal_id: UUID
    ) -> ActiveRoadmapRow | None:
        row = (
            (
                await session.execute(
                    select(
                        roadmaps.c.id,
                        roadmaps.c.version,
                        roadmaps.c.proposal_id,
                        roadmaps.c.profile_version,
                        roadmaps.c.release_id,
                        roadmaps.c.title,
                        roadmaps.c.validity,
                    ).where(
                        roadmaps.c.user_id == user_id,
                        roadmaps.c.goal_id == goal_id,
                        roadmaps.c.state == "ACTIVE",
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return ActiveRoadmapRow(
            view=DashboardRoadmapView(
                roadmap_id=row["id"],
                roadmap_version=row["version"],
                proposal_id=row["proposal_id"],
                profile_version=row["profile_version"],
                release_id=row["release_id"],
                title=row["title"],
            ),
            validity=row["validity"],
        )

    async def _recompute(
        self, session: AsyncSession, user_id: UUID, goal_id: UUID
    ) -> DashboardRecomputeView | None:
        row = (
            (
                await session.execute(
                    select(
                        recompute_requests.c.id,
                        recompute_requests.c.profile_version,
                        recompute_requests.c.state,
                        recompute_requests.c.error_code,
                    )
                    .join(profiles, profiles.c.user_id == recompute_requests.c.user_id)
                    .join(
                        recompute_contexts,
                        recompute_contexts.c.request_id == recompute_requests.c.id,
                    )
                    .where(
                        recompute_requests.c.user_id == user_id,
                        recompute_requests.c.profile_version == profiles.c.version,
                        recompute_contexts.c.payload["goal_id"].astext == str(goal_id),
                    )
                    .order_by(recompute_requests.c.updated_at.desc(), recompute_requests.c.id)
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return DashboardRecomputeView(
            recompute_request_id=row["id"],
            profile_version=row["profile_version"],
            state=row["state"],
            error_code=row["error_code"],
        )

    async def _events(
        self, session: AsyncSession, user_id: UUID
    ) -> tuple[DashboardEventView, ...]:
        rows = (
            (
                await session.execute(
                    select(
                        user_state_events.c.kind,
                        user_state_events.c.version,
                        user_state_events.c.created_at,
                    )
                    .where(user_state_events.c.user_id == user_id)
                    .order_by(user_state_events.c.created_at.desc(), user_state_events.c.id)
                    .limit(10)
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            DashboardEventView(
                kind=row["kind"], version=row["version"], created_at=row["created_at"]
            )
            for row in rows
        )

def _dashboard_state(
    recompute: DashboardRecomputeView | None, roadmap: DashboardRoadmapView | None
) -> tuple[Literal["EMPTY", "RECOMPUTING", "READY", "RECOMPUTE_FAILED"], tuple[str, ...]]:
    if recompute is not None and recompute.state in {"PENDING", "RUNNING"}:
        return "RECOMPUTING", ()
    if recompute is not None and recompute.state == "FAILED":
        return "RECOMPUTE_FAILED", ("RECOMPUTE_FAILED",)
    if roadmap is not None:
        return "READY", ()
    return "EMPTY", ()

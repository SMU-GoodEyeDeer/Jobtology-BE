from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from jobtology_be.application.queries import (
    AnalysisView,
    CapabilityView,
    GoalView,
    ProfileView,
    RecomputeView,
    RoadmapDiffView,
    RoadmapScheduleChangeView,
    RoadmapStepView,
    RoadmapView,
)
from jobtology_be.infrastructure.persistence.contracts import MissingRecordError
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    analyses,
    goals,
    profiles,
    recompute_requests,
    roadmap_steps,
    roadmaps,
    route_proposals,
    step_dependencies,
    user_capabilities,
)


class PostgresProductQueries:
    _database: Database

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_profile(self, user_id: UUID) -> ProfileView:
        async with self._database.sessions() as session:
            row = (
                await session.execute(
                    select(
                        profiles.c.user_id,
                        profiles.c.version,
                        profiles.c.major_raw,
                        profiles.c.major_concept_id,
                        profiles.c.year,
                        profiles.c.enrollment_status,
                        profiles.c.expected_graduation_on,
                    ).where(profiles.c.user_id == user_id)
                )
            ).one_or_none()
        if row is None:
            raise MissingRecordError(resource="profile")
        return ProfileView(
            user_id=row.user_id,
            profile_version=row.version,
            major_raw=row.major_raw,
            major_concept_id=row.major_concept_id,
            year=row.year,
            enrollment_status=row.enrollment_status,
            expected_graduation_on=row.expected_graduation_on,
        )

    async def list_goals(self, user_id: UUID) -> tuple[GoalView, ...]:
        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(
                        goals.c.id,
                        goals.c.goal_mode,
                        goals.c.occupation_id,
                        goals.c.target_by,
                        goals.c.timezone,
                        goals.c.original_time_phrase,
                        goals.c.status,
                        goals.c.created_at,
                        goals.c.updated_at,
                    )
                    .where(goals.c.user_id == user_id)
                    .order_by(goals.c.updated_at.desc(), goals.c.id)
                )
            ).all()
        return tuple(self._goal_view(row) for row in rows)

    async def get_goal(self, user_id: UUID, goal_id: UUID) -> GoalView:
        async with self._database.sessions() as session:
            row = (
                await session.execute(
                    select(
                        goals.c.id,
                        goals.c.goal_mode,
                        goals.c.occupation_id,
                        goals.c.target_by,
                        goals.c.timezone,
                        goals.c.original_time_phrase,
                        goals.c.status,
                        goals.c.created_at,
                        goals.c.updated_at,
                    ).where(goals.c.id == goal_id, goals.c.user_id == user_id)
                )
            ).one_or_none()
        if row is None:
            raise MissingRecordError(resource="goal")
        return self._goal_view(row)

    async def list_capabilities(self, user_id: UUID) -> tuple[CapabilityView, ...]:
        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(
                        user_capabilities.c.id,
                        user_capabilities.c.category,
                        user_capabilities.c.raw_text,
                        user_capabilities.c.entity_id,
                        user_capabilities.c.proficiency,
                        user_capabilities.c.verification,
                        user_capabilities.c.lifecycle,
                        user_capabilities.c.details,
                        user_capabilities.c.source_completion_event_id,
                        user_capabilities.c.created_at,
                        user_capabilities.c.updated_at,
                    )
                    .where(user_capabilities.c.user_id == user_id)
                    .order_by(user_capabilities.c.updated_at.desc(), user_capabilities.c.id)
                )
            ).all()
        return tuple(self._capability_view(row) for row in rows)

    async def get_capability(self, user_id: UUID, capability_id: UUID) -> CapabilityView:
        async with self._database.sessions() as session:
            row = (
                await session.execute(
                    select(
                        user_capabilities.c.id,
                        user_capabilities.c.category,
                        user_capabilities.c.raw_text,
                        user_capabilities.c.entity_id,
                        user_capabilities.c.proficiency,
                        user_capabilities.c.verification,
                        user_capabilities.c.lifecycle,
                        user_capabilities.c.details,
                        user_capabilities.c.source_completion_event_id,
                        user_capabilities.c.created_at,
                        user_capabilities.c.updated_at,
                    ).where(
                        user_capabilities.c.id == capability_id,
                        user_capabilities.c.user_id == user_id,
                    )
                )
            ).one_or_none()
        if row is None:
            raise MissingRecordError(resource="capability")
        return self._capability_view(row)

    @staticmethod
    def _goal_view(row) -> GoalView:
        return GoalView(
            goal_id=row.id,
            goal_mode=row.goal_mode,
            occupation_id=row.occupation_id,
            target_by=row.target_by,
            timezone=row.timezone,
            original_time_phrase=row.original_time_phrase,
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _capability_view(row) -> CapabilityView:
        return CapabilityView(
            capability_id=row.id,
            category=row.category,
            raw_text=row.raw_text,
            entity_id=row.entity_id,
            proficiency=row.proficiency,
            verification=row.verification,
            lifecycle=row.lifecycle,
            details=row.details,
            source_completion_event_id=row.source_completion_event_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def list_roadmaps(self, user_id: UUID) -> tuple[RoadmapView, ...]:
        async with self._database.sessions() as session:
            roadmap_ids = tuple(
                (await session.scalars(
                    select(roadmaps.c.id)
                    .where(roadmaps.c.user_id == user_id)
                    .order_by(roadmaps.c.updated_at.desc(), roadmaps.c.id)
                )).all()
            )
        result: list[RoadmapView] = []
        for roadmap_id in roadmap_ids:
            result.append(await self.get_roadmap(user_id, roadmap_id))
        return tuple(result)

    async def get_roadmap(self, user_id: UUID, roadmap_id: UUID) -> RoadmapView:
        async with self._database.sessions() as session:
            roadmap = (
                await session.execute(
                    select(
                        roadmaps.c.id,
                        roadmaps.c.version,
                        roadmaps.c.state,
                        roadmaps.c.goal_id,
                        roadmaps.c.proposal_id,
                        roadmaps.c.title,
                        roadmaps.c.profile_version,
                        roadmaps.c.release_id,
                        roadmaps.c.validity,
                    ).where(roadmaps.c.id == roadmap_id, roadmaps.c.user_id == user_id)
                )
            ).one_or_none()
            if roadmap is None:
                raise MissingRecordError(resource="roadmap")
            step_rows = (
                await session.execute(
                    select(
                        roadmap_steps.c.id,
                        roadmap_steps.c.step_key,
                        roadmap_steps.c.position,
                        roadmap_steps.c.action_id,
                        roadmap_steps.c.template_revision,
                        roadmap_steps.c.state,
                        roadmap_steps.c.planned_start,
                        roadmap_steps.c.planned_end,
                        roadmap_steps.c.outcomes,
                        roadmap_steps.c.criteria,
                    )
                    .where(roadmap_steps.c.roadmap_id == roadmap_id)
                    .order_by(roadmap_steps.c.position, roadmap_steps.c.id)
                )
            ).all()
            dependencies = (
                await session.execute(
                    select(step_dependencies.c.step_id, step_dependencies.c.prerequisite_step_id).where(
                        step_dependencies.c.roadmap_id == roadmap_id
                    )
                )
            ).all()
        prerequisite_ids: dict[UUID, list[UUID]] = {}
        for dependency in dependencies:
            prerequisite_ids.setdefault(dependency.step_id, []).append(
                dependency.prerequisite_step_id
            )
        return RoadmapView(
            roadmap_id=roadmap.id,
            roadmap_version=roadmap.version,
            state=roadmap.state,
            goal_id=roadmap.goal_id,
            proposal_id=roadmap.proposal_id,
            title=roadmap.title,
            profile_version=roadmap.profile_version,
            release_id=roadmap.release_id,
            validity=roadmap.validity,
            steps=tuple(
                RoadmapStepView(
                    step_id=row.id,
                    step_key=row.step_key,
                    position=row.position,
                    action_id=row.action_id,
                    template_revision=row.template_revision,
                    state=row.state,
                    planned_start=row.planned_start,
                    planned_end=row.planned_end,
                    outcomes=tuple(row.outcomes),
                    criteria=tuple(row.criteria),
                    prerequisite_step_ids=tuple(prerequisite_ids.get(row.id, [])),
                )
                for row in step_rows
            ),
        )

    async def get_roadmap_diff(
        self, user_id: UUID, roadmap_id: UUID, proposal_id: UUID
    ) -> RoadmapDiffView:
        async with self._database.sessions() as session:
            roadmap = (
                await session.execute(
                    select(roadmaps.c.id, roadmaps.c.version, roadmaps.c.goal_id, roadmaps.c.proposal_id)
                    .where(roadmaps.c.id == roadmap_id, roadmaps.c.user_id == user_id)
                )
            ).one_or_none()
            if roadmap is None:
                raise MissingRecordError(resource="roadmap")
            old_constraints = await session.scalar(
                select(route_proposals.c.constraints_snapshot).where(
                    route_proposals.c.id == roadmap.proposal_id,
                    route_proposals.c.user_id == user_id,
                )
            )
            proposal = (
                await session.execute(
                    select(
                        route_proposals.c.proposal_hash,
                        route_proposals.c.constraints_snapshot,
                        route_proposals.c.steps,
                    )
                    .join(analyses, analyses.c.id == route_proposals.c.analysis_id)
                    .where(
                        route_proposals.c.id == proposal_id,
                        route_proposals.c.user_id == user_id,
                        analyses.c.goal_id == roadmap.goal_id,
                    )
                )
            ).one_or_none()
            if old_constraints is None or proposal is None:
                raise MissingRecordError(resource="route proposal")
            current_steps = (
                await session.execute(
                    select(
                        roadmap_steps.c.step_key,
                        roadmap_steps.c.position,
                        roadmap_steps.c.planned_start,
                        roadmap_steps.c.planned_end,
                    )
                    .where(roadmap_steps.c.roadmap_id == roadmap_id)
                    .order_by(roadmap_steps.c.position, roadmap_steps.c.id)
                )
            ).all()
        old_steps = {step.step_key: step for step in current_steps}
        proposal_steps = {step["step_key"]: step for step in proposal.steps}
        retained_step_keys = tuple(key for key in proposal_steps if key in old_steps)
        old_retained_order = tuple(key for key in old_steps if key in proposal_steps)
        reordered_step_keys = (
            ()
            if old_retained_order == retained_step_keys
            else tuple(
                key
                for key, old_index in ((key, old_retained_order.index(key)) for key in retained_step_keys)
                if old_index != retained_step_keys.index(key)
            )
        )
        schedule_changes = tuple(
            RoadmapScheduleChangeView(
                step_key=key,
                old_planned_start=old_steps[key].planned_start,
                old_planned_end=old_steps[key].planned_end,
                new_planned_start=datetime.fromisoformat(proposal_steps[key]["planned_start_at"]),
                new_planned_end=datetime.fromisoformat(proposal_steps[key]["planned_end_at"]),
            )
            for key in retained_step_keys
            if (
                old_steps[key].planned_start
                != datetime.fromisoformat(proposal_steps[key]["planned_start_at"])
                or old_steps[key].planned_end
                != datetime.fromisoformat(proposal_steps[key]["planned_end_at"])
            )
        )
        return RoadmapDiffView(
            roadmap_id=roadmap.id,
            roadmap_version=roadmap.version,
            proposal_id=proposal_id,
            proposal_hash=proposal.proposal_hash,
            retained_step_keys=retained_step_keys,
            added_step_keys=tuple(key for key in proposal_steps if key not in old_steps),
            removed_step_keys=tuple(key for key in old_steps if key not in proposal_steps),
            reordered_step_keys=reordered_step_keys,
            schedule_changes=schedule_changes,
            constraint_changes={
                key: (old_constraints.get(key), proposal.constraints_snapshot.get(key))
                for key in sorted(set(old_constraints) | set(proposal.constraints_snapshot))
                if old_constraints.get(key) != proposal.constraints_snapshot.get(key)
            },
        )

    async def request_analysis(
        self, user_id: UUID, goal_id: UUID, expected_profile_version: int
    ) -> RecomputeView:
        async with self._database.sessions() as session:
            goal = await session.scalar(
                select(goals.c.id).where(goals.c.id == goal_id, goals.c.user_id == user_id)
            )
            if goal is None:
                raise MissingRecordError(resource="goal")
            recompute_request_id = await session.scalar(
                select(recompute_requests.c.id)
                .where(
                    recompute_requests.c.user_id == user_id,
                    recompute_requests.c.profile_version == expected_profile_version,
                )
                .order_by(recompute_requests.c.created_at.desc(), recompute_requests.c.id)
                .limit(1)
            )
        if recompute_request_id is None:
            raise MissingRecordError(resource="recompute request")
        return await self.get_recompute(user_id, recompute_request_id)

    async def get_recompute(self, user_id: UUID, recompute_request_id: UUID) -> RecomputeView:
        async with self._database.sessions() as session:
            row = (
                await session.execute(
                    select(
                        recompute_requests.c.id,
                        recompute_requests.c.profile_version,
                        recompute_requests.c.state,
                        recompute_requests.c.resulting_analysis_id,
                        recompute_requests.c.proposal_id,
                        recompute_requests.c.error_code,
                    ).where(
                        recompute_requests.c.id == recompute_request_id,
                        recompute_requests.c.user_id == user_id,
                    )
                )
            ).one_or_none()
        if row is None:
            raise MissingRecordError(resource="recompute request")
        return RecomputeView(
            recompute_request_id=row.id,
            profile_version=row.profile_version,
            state=row.state,
            resulting_analysis_id=row.resulting_analysis_id,
            proposal_id=row.proposal_id,
            error_code=row.error_code,
        )

    async def get_analysis(self, user_id: UUID, analysis_id: UUID) -> AnalysisView:
        async with self._database.sessions() as session:
            row = (
                await session.execute(
                    select(
                        analyses.c.id,
                        analyses.c.goal_id,
                        analyses.c.profile_version,
                        analyses.c.basis_type,
                        analyses.c.basis_version,
                        analyses.c.release_id,
                        analyses.c.methodology_version,
                        analyses.c.status,
                        analyses.c.reference_at,
                        analyses.c.results,
                    ).where(analyses.c.id == analysis_id, analyses.c.user_id == user_id)
                )
            ).one_or_none()
        if row is None:
            raise MissingRecordError(resource="analysis")
        return AnalysisView(
            analysis_id=row.id,
            goal_id=row.goal_id,
            profile_version=row.profile_version,
            basis_type=row.basis_type,
            basis_version=row.basis_version,
            release_id=row.release_id,
            methodology_version=row.methodology_version,
            status=row.status,
            reference_at=row.reference_at,
            results=row.results,
        )

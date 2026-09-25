from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.contracts import RoadmapOutcome
from jobtology_be.infrastructure.persistence.configured_recompute import (
    ConfiguredRecomputeRepository,
)
from jobtology_be.infrastructure.persistence.contracts import (
    AnalysisPersist,
    AnalysisRecomputeSubmission,
    AnalysisSnapshot,
    CapabilityDelete,
    CapabilityMutation,
    CapabilitySnapshot,
    GoalMutation,
    GoalSnapshot,
    IdempotencyAcquire,
    IdempotencyComplete,
    IdempotencySnapshot,
    JsonValue,
    MissingRecordError,
    PersistenceConflictError,
    ProfileMutation,
    ProfileSnapshot,
    RecomputeRequestCreate,
    RecomputeRequestSnapshot,
    RoadmapCreate,
    RoadmapMutation,
    RoadmapSnapshot,
    RoutePreferenceMutation,
    RouteProposalPersist,
    RouteProposalSnapshot,
    StepStateMutation,
    UserCreate,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.idempotency import (
    acquire_idempotency as acquire_idempotency_record,
)
from jobtology_be.infrastructure.persistence.idempotency import (
    complete_idempotency as complete_idempotency_record,
)
from jobtology_be.infrastructure.persistence.outbox import OutboxRepository
from jobtology_be.infrastructure.persistence.recompute_requests import RecomputeRequestRepository
from jobtology_be.infrastructure.persistence.roadmap_source_validity import (
    parse_source_validity,
    source_is_current,
)
from jobtology_be.infrastructure.persistence.roadmap_steps import RoadmapStepRepository
from jobtology_be.infrastructure.persistence.schema import (
    analyses,
    calculation_traces,
    goals,
    profiles,
    roadmap_steps,
    roadmaps,
    route_preferences,
    route_proposals,
    step_completion_inheritances,
    step_dependencies,
    user_capabilities,
    user_state_events,
    users,
)
from jobtology_be.infrastructure.persistence.worker import WorkerRepository


class PostgresApplicationStore(OutboxRepository, WorkerRepository):
    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self._active_session: ContextVar[AsyncSession | None] = ContextVar(
            "postgres_application_store_active_session", default=None
        )
        self._configured_recomputes = ConfiguredRecomputeRepository(database)
        self._roadmap_steps = RoadmapStepRepository(database)
        self._recompute_requests = RecomputeRequestRepository(database)

    async def create_user(self, request: UserCreate) -> ProfileSnapshot:
        async with self._transaction() as session:
            await session.execute(
                postgres_insert(users).values(id=request.user_id).on_conflict_do_nothing()
            )
            await session.execute(
                postgres_insert(profiles).values(user_id=request.user_id).on_conflict_do_nothing()
            )
            version = await session.scalar(
                select(profiles.c.version).where(profiles.c.user_id == request.user_id)
            )
            if version is None:
                raise MissingRecordError(resource="profile")
        return ProfileSnapshot(user_id=request.user_id, version=version)

    async def mutate_profile(self, request: ProfileMutation) -> ProfileSnapshot:
        async with self._database.sessions.begin() as session:
            statement = (
                update(profiles)
                .where(
                    profiles.c.user_id == request.user_id,
                    profiles.c.version == request.expected_version,
                )
                .values(
                    major_raw=request.major_raw,
                    major_concept_id=request.major_concept_id,
                    year=request.year,
                    enrollment_status=request.enrollment_status,
                    expected_graduation_on=request.expected_graduation_on,
                    version=profiles.c.version + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            result = await session.execute(statement)
            if result.rowcount != 1:
                raise PersistenceConflictError(resource="profile")
            event_id = await self._append_event(
                session,
                request.user_id,
                request.user_id,
                request.expected_version + 1,
                "PROFILE_UPDATED",
            )
            await self._configured_recomputes.enqueue_for_active_user(
                session, request.user_id, request.expected_version + 1, event_id
            )
        return ProfileSnapshot(user_id=request.user_id, version=request.expected_version + 1)

    async def mutate_goal(self, request: GoalMutation) -> GoalSnapshot:
        goal_id = request.goal_id or uuid4()
        async with self._transaction() as session:
            profile_version = await session.scalar(
                select(profiles.c.version)
                .where(profiles.c.user_id == request.user_id)
                .with_for_update()
            )
            if profile_version != request.expected_profile_version:
                raise PersistenceConflictError(resource="profile")
            if request.status == "ACTIVE":
                other_active_goal_id = await session.scalar(
                    select(goals.c.id)
                    .where(
                        goals.c.user_id == request.user_id,
                        goals.c.status == "ACTIVE",
                        goals.c.id != goal_id,
                    )
                    .with_for_update()
                )
                if other_active_goal_id is not None:
                    raise PersistenceConflictError(resource="goal")
            await self._bump_profile(session, request.user_id, request.expected_profile_version)
            if request.goal_id is None:
                await session.execute(
                    insert(goals).values(
                        id=goal_id,
                        user_id=request.user_id,
                        goal_mode=request.goal_mode,
                        occupation_id=request.occupation_id,
                        target_by=request.target_by,
                        timezone=request.timezone,
                        original_time_phrase=request.original_time_phrase,
                        status=request.status,
                    )
                )
            else:
                result = await session.execute(
                    update(goals)
                    .where(goals.c.id == goal_id, goals.c.user_id == request.user_id)
                    .values(
                        goal_mode=request.goal_mode,
                        occupation_id=request.occupation_id,
                        target_by=request.target_by,
                        timezone=request.timezone,
                        original_time_phrase=request.original_time_phrase,
                        status=request.status,
                        updated_at=datetime.now(UTC),
                    )
                )
                if result.rowcount != 1:
                    raise MissingRecordError(resource="goal")
            event_id = await self._append_event(
                session,
                request.user_id,
                goal_id,
                request.expected_profile_version + 1,
                "GOAL_UPDATED",
            )
            await self._configured_recomputes.enqueue_for_goal(
                session,
                request.user_id,
                request.expected_profile_version + 1,
                event_id,
                goal_id,
            )
        return GoalSnapshot(goal_id=goal_id, user_id=request.user_id, status=request.status)

    async def mutate_capability(self, request: CapabilityMutation) -> CapabilitySnapshot:
        capability_id = request.capability_id or uuid4()
        async with self._transaction() as session:
            await self._bump_profile(session, request.user_id, request.expected_profile_version)
            values = {
                "user_id": request.user_id,
                "category": request.category,
                "raw_text": request.raw_text,
                "entity_id": request.entity_id,
                "proficiency": request.proficiency,
                "details": dict(request.details),
                "updated_at": datetime.now(UTC),
            }
            if request.capability_id is None:
                await session.execute(insert(user_capabilities).values(id=capability_id, **values))
            else:
                result = await session.execute(
                    update(user_capabilities)
                    .where(
                        user_capabilities.c.id == capability_id,
                        user_capabilities.c.user_id == request.user_id,
                    )
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise MissingRecordError(resource="capability")
            event_id = await self._append_event(
                session,
                request.user_id,
                capability_id,
                request.expected_profile_version + 1,
                "CAPABILITY_UPDATED",
            )
            await self._configured_recomputes.enqueue_for_active_user(
                session, request.user_id, request.expected_profile_version + 1, event_id
            )
        return CapabilitySnapshot(
            capability_id=capability_id,
            profile_version=request.expected_profile_version + 1,
        )

    async def delete_capability(self, request: CapabilityDelete) -> ProfileSnapshot:
        async with self._database.sessions.begin() as session:
            await self._bump_profile(session, request.user_id, request.expected_profile_version)
            result = await session.execute(
                update(user_capabilities)
                .where(
                    user_capabilities.c.id == request.capability_id,
                    user_capabilities.c.user_id == request.user_id,
                )
                .values(lifecycle="REVOKED", updated_at=datetime.now(UTC))
            )
            if result.rowcount != 1:
                raise MissingRecordError(resource="capability")
            event_id = await self._append_event(
                session,
                request.user_id,
                request.capability_id,
                request.expected_profile_version + 1,
                "CAPABILITY_REVOKED",
            )
            await self._configured_recomputes.enqueue_for_active_user(
                session, request.user_id, request.expected_profile_version + 1, event_id
            )
        return ProfileSnapshot(
            user_id=request.user_id, version=request.expected_profile_version + 1
        )

    async def set_route_preferences(self, request: RoutePreferenceMutation) -> ProfileSnapshot:
        async with self._database.sessions.begin() as session:
            await self._bump_profile(session, request.user_id, request.expected_profile_version)
            statement = (
                postgres_insert(route_preferences)
                .values(
                    user_id=request.user_id,
                    available_hours_per_week=request.available_hours_per_week,
                    availability_source=request.availability_source,
                    budget_mode=request.budget_mode,
                    max_out_of_pocket_krw=request.max_out_of_pocket_krw,
                    fastest_path=request.fastest_path,
                    needs_portfolio=request.needs_portfolio,
                    career_switch=request.career_switch,
                )
                .on_conflict_do_update(
                    index_elements=[route_preferences.c.user_id],
                    set_={
                        "available_hours_per_week": request.available_hours_per_week,
                        "availability_source": request.availability_source,
                        "budget_mode": request.budget_mode,
                        "max_out_of_pocket_krw": request.max_out_of_pocket_krw,
                        "fastest_path": request.fastest_path,
                        "needs_portfolio": request.needs_portfolio,
                        "career_switch": request.career_switch,
                        "updated_at": datetime.now(UTC),
                    },
                )
            )
            await session.execute(statement)
            event_id = await self._append_event(
                session,
                request.user_id,
                request.user_id,
                request.expected_profile_version + 1,
                "ROUTE_PREFERENCES_UPDATED",
            )
            await self._configured_recomputes.enqueue_for_active_user(
                session, request.user_id, request.expected_profile_version + 1, event_id
            )
        return ProfileSnapshot(
            user_id=request.user_id, version=request.expected_profile_version + 1
        )

    async def create_recompute_request(
        self, request: RecomputeRequestCreate
    ) -> RecomputeRequestSnapshot:
        return await self._recompute_requests.create_recompute_request(request)

    async def submit_analysis_recompute(
        self, submission: AnalysisRecomputeSubmission
    ) -> RecomputeRequestSnapshot:
        session = self._active_session.get()
        if session is None:
            return await self._recompute_requests.submit_analysis_recompute(submission)
        return await self._recompute_requests.submit_analysis_recompute_in_session(session, submission)

    async def persist_analysis(self, request: AnalysisPersist) -> AnalysisSnapshot:
        async with self._database.sessions.begin() as session:
            goal_owner = await session.scalar(
                select(goals.c.user_id).where(goals.c.id == request.goal_id)
            )
            if goal_owner != request.user_id:
                raise MissingRecordError(resource="goal")
            await session.execute(
                insert(analyses).values(
                    id=request.analysis_id,
                    user_id=request.user_id,
                    goal_id=request.goal_id,
                    profile_version=request.profile_version,
                    basis_type=request.basis_type,
                    basis_version=request.basis_version,
                    release_id=request.release_id,
                    methodology_version=request.methodology_version,
                    status=request.status,
                    reference_at=request.reference_at,
                    input_snapshot=dict(request.input_snapshot),
                    input_hash=request.input_hash,
                    results=None if request.results is None else dict(request.results),
                    is_fixture=request.is_fixture,
                    generated_at=datetime.now(UTC),
                )
            )
            await session.execute(
                update(profiles)
                .where(
                    profiles.c.user_id == request.user_id,
                    profiles.c.version == request.profile_version,
                )
                .values(latest_analysis_id=request.analysis_id)
            )
        return AnalysisSnapshot(
            analysis_id=request.analysis_id,
            profile_version=request.profile_version,
            status=request.status,
        )

    async def persist_route_proposal(self, request: RouteProposalPersist) -> RouteProposalSnapshot:
        async with self._database.sessions.begin() as session:
            analysis_owner = await session.execute(
                select(analyses.c.user_id, analyses.c.profile_version).where(
                    analyses.c.id == request.analysis_id
                )
            )
            analysis_row = analysis_owner.one_or_none()
            if analysis_row is None or analysis_row.user_id != request.user_id:
                raise MissingRecordError(resource="analysis")
            if analysis_row.profile_version != request.profile_version:
                raise PersistenceConflictError(resource="analysis profile version")
            await session.execute(
                insert(calculation_traces).values(
                    id=request.trace_id,
                    user_id=request.user_id,
                    kind="ROUTE",
                    input_hash=request.proposal_hash,
                    versions=dict(request.trace_versions),
                    release_id=request.release_id,
                    outputs=dict(request.trace_outputs),
                )
            )
            await session.execute(
                insert(route_proposals).values(
                    id=request.proposal_id,
                    analysis_id=request.analysis_id,
                    user_id=request.user_id,
                    proposal_hash=request.proposal_hash,
                    profile_version=request.profile_version,
                    constraints_snapshot=dict(request.constraints_snapshot),
                    feasibility=request.feasibility,
                    optimization_status=request.optimization_status,
                    steps=[dict(step) for step in request.steps],
                    decision_trace_id=request.trace_id,
                )
            )
        return RouteProposalSnapshot(
            proposal_id=request.proposal_id, feasibility=request.feasibility
        )

    async def create_roadmap(self, request: RoadmapCreate) -> RoadmapSnapshot:
        async with self._transaction() as session:
            profile_version = await session.scalar(
                select(profiles.c.version)
                .where(profiles.c.user_id == request.user_id)
                .with_for_update()
            )
            if profile_version != request.profile_version:
                raise PersistenceConflictError(resource="profile")
            goal_owner = await session.scalar(
                select(goals.c.user_id).where(goals.c.id == request.goal_id)
            )
            if goal_owner != request.user_id:
                raise MissingRecordError(resource="goal")
            proposal = await session.execute(
                select(
                    route_proposals.c.user_id,
                    route_proposals.c.profile_version,
                    route_proposals.c.feasibility,
                    route_proposals.c.steps,
                    analyses.c.goal_id,
                    analyses.c.release_id,
                )
                .join(analyses, analyses.c.id == route_proposals.c.analysis_id)
                .where(
                    route_proposals.c.id == request.proposal_id,
                    analyses.c.user_id == request.user_id,
                )
            )
            proposal_row = proposal.one_or_none()
            if proposal_row is None or proposal_row.user_id != request.user_id:
                raise MissingRecordError(resource="route proposal")
            if proposal_row.profile_version != request.profile_version:
                raise PersistenceConflictError(resource="route proposal profile version")
            if proposal_row.feasibility == "INFEASIBLE":
                raise PersistenceConflictError(resource="infeasible route proposal")
            if proposal_row.goal_id != request.goal_id:
                raise PersistenceConflictError(resource="route proposal goal")
            previous_active_roadmap_id = await session.scalar(
                select(roadmaps.c.id)
                .where(
                    roadmaps.c.user_id == request.user_id,
                    roadmaps.c.goal_id == request.goal_id,
                    roadmaps.c.state == "ACTIVE",
                )
                .with_for_update()
            )
            await session.execute(
                insert(roadmaps).values(
                    id=request.roadmap_id,
                    user_id=request.user_id,
                    goal_id=request.goal_id,
                    proposal_id=request.proposal_id,
                    title=request.title,
                    profile_version=request.profile_version,
                    release_id=proposal_row.release_id,
                )
            )
            step_ids: dict[str, UUID] = {}
            for position, proposal_step in enumerate(proposal_row.steps):
                step_id = uuid4()
                step_key = proposal_step["step_key"]
                step_ids[step_key] = step_id
                await session.execute(
                    insert(roadmap_steps).values(
                        id=step_id,
                        roadmap_id=request.roadmap_id,
                        step_key=step_key,
                        position=position,
                        action_id=proposal_step["action_id"],
                        template_revision=proposal_step["template_revision"],
                        outcomes=proposal_step["outcomes"],
                        criteria=proposal_step.get("completion_criteria", []),
                    )
                )
            for proposal_step in proposal_row.steps:
                step_id = step_ids[proposal_step["step_key"]]
                for prerequisite_key in proposal_step.get("prerequisite_step_keys", []):
                    prerequisite_id = step_ids.get(prerequisite_key)
                    if prerequisite_id is None:
                        raise PersistenceConflictError(resource="proposal dependency")
                    await session.execute(
                        insert(step_dependencies).values(
                            roadmap_id=request.roadmap_id,
                            step_id=step_id,
                            prerequisite_step_id=prerequisite_id,
                    )
                )
            if previous_active_roadmap_id is not None:
                completed_steps = await session.execute(
                    select(
                        roadmap_steps.c.action_id,
                        roadmap_steps.c.template_revision,
                        roadmap_steps.c.outcomes,
                        roadmap_steps.c.criteria,
                        step_completion_inheritances.c.original_completion_event_id,
                    )
                    .join(
                        step_completion_inheritances,
                        step_completion_inheritances.c.step_id == roadmap_steps.c.id,
                    )
                    .where(
                        roadmap_steps.c.roadmap_id == previous_active_roadmap_id,
                        roadmap_steps.c.state == "COMPLETED",
                    )
                )
                completed_by_semantics = {
                    (
                        completed_step.action_id,
                        completed_step.template_revision,
                        _outcome_semantics(completed_step.outcomes),
                        tuple(completed_step.criteria),
                    ): completed_step.original_completion_event_id
                    for completed_step in completed_steps
                }
                for proposal_step in proposal_row.steps:
                    completion_event_id = completed_by_semantics.get(
                        (
                            proposal_step["action_id"],
                            proposal_step["template_revision"],
                            _outcome_semantics(proposal_step["outcomes"]),
                            tuple(proposal_step.get("completion_criteria", [])),
                        )
                    )
                    if completion_event_id is None:
                        continue
                    step_id = step_ids[proposal_step["step_key"]]
                    await session.execute(
                        update(roadmap_steps)
                        .where(roadmap_steps.c.id == step_id)
                        .values(state="COMPLETED", updated_at=datetime.now(UTC))
                    )
                    await session.execute(
                        insert(step_completion_inheritances).values(
                            step_id=step_id,
                            original_completion_event_id=completion_event_id,
                        )
                    )
        return RoadmapSnapshot(roadmap_id=request.roadmap_id, version=1, state="DRAFT")

    async def mutate_roadmap(self, request: RoadmapMutation) -> RoadmapSnapshot:
        async with self._database.sessions.begin() as session:
            profile_version = await session.scalar(
                select(profiles.c.version)
                .where(profiles.c.user_id == request.user_id)
                .with_for_update()
            )
            if profile_version != request.expected_profile_version:
                raise PersistenceConflictError(resource="profile")
            roadmap = await session.execute(
                select(roadmaps.c.state, roadmaps.c.proposal_id, roadmaps.c.validity)
                .where(
                    roadmaps.c.id == request.roadmap_id,
                    roadmaps.c.user_id == request.user_id,
                    roadmaps.c.version == request.expected_roadmap_version,
                )
                .with_for_update()
            )
            roadmap_row = roadmap.one_or_none()
            if roadmap_row is None:
                raise PersistenceConflictError(resource="roadmap")
            if request.state == "ACTIVE":
                source_validity = parse_source_validity(roadmap_row.validity)
                if not source_is_current(source_validity, datetime.now(UTC)):
                    raise PersistenceConflictError(resource="roadmap source")
                feasibility = await session.scalar(
                    select(route_proposals.c.feasibility).where(
                        route_proposals.c.id == roadmap_row.proposal_id,
                        route_proposals.c.user_id == request.user_id,
                    )
                )
                if feasibility not in {"FEASIBLE", "RISKY"}:
                    raise PersistenceConflictError(resource="route proposal")
                await session.execute(
                    update(roadmaps)
                    .where(
                        roadmaps.c.user_id == request.user_id,
                        roadmaps.c.state == "ACTIVE",
                        roadmaps.c.id != request.roadmap_id,
                    )
                    .values(
                        state="ARCHIVED",
                        version=roadmaps.c.version + 1,
                        updated_at=datetime.now(UTC),
                    )
                )
            result = await session.execute(
                update(roadmaps)
                .where(
                    roadmaps.c.id == request.roadmap_id,
                    roadmaps.c.user_id == request.user_id,
                    roadmaps.c.version == request.expected_roadmap_version,
                )
                .values(
                    state=request.state,
                    title=roadmaps.c.title if request.title is None else request.title,
                    version=roadmaps.c.version + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            if result.rowcount != 1:
                raise PersistenceConflictError(resource="roadmap")
        return RoadmapSnapshot(
            roadmap_id=request.roadmap_id,
            version=request.expected_roadmap_version + 1,
            state=request.state,
        )

    async def mutate_step_state(self, request: StepStateMutation) -> RoadmapSnapshot:
        return await self._roadmap_steps.mutate_step_state(request)

    async def acquire_idempotency(self, request: IdempotencyAcquire) -> IdempotencySnapshot:
        async with self._database.sessions.begin() as session:
            return await acquire_idempotency_record(session, request)

    async def complete_idempotency(self, request: IdempotencyComplete) -> None:
        async with self._database.sessions.begin() as session:
            await complete_idempotency_record(session, request)

    async def execute_idempotency(
        self,
        request: IdempotencyAcquire,
        response_status: int,
        operation: Callable[[], Awaitable[Mapping[str, JsonValue]]],
    ) -> IdempotencySnapshot:
        async with self._database.sessions.begin() as session:
            snapshot = await acquire_idempotency_record(session, request)
            if snapshot.response_status is not None:
                return snapshot
            active_session_token = self._active_session.set(session)
            try:
                response = await operation()
            finally:
                self._active_session.reset(active_session_token)
            await complete_idempotency_record(
                session,
                IdempotencyComplete(
                    request=request,
                    response_status=response_status,
                    response=response,
                ),
            )
            return IdempotencySnapshot(response_status=response_status, response=response)

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        session = self._active_session.get()
        if session is not None:
            yield session
            return
        async with self._database.sessions.begin() as new_session:
            yield new_session

    async def _bump_profile(self, session, user_id: UUID, expected_version: int) -> None:
        result = await session.execute(
            update(profiles)
            .where(profiles.c.user_id == user_id, profiles.c.version == expected_version)
            .values(version=profiles.c.version + 1, updated_at=datetime.now(UTC))
        )
        if result.rowcount != 1:
            raise PersistenceConflictError(resource="profile")

    async def _append_event(
        self,
        session,
        user_id: UUID,
        aggregate_id: UUID,
        version: int,
        kind: str,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> UUID:
        event_id = uuid4()
        await session.execute(
            insert(user_state_events).values(
                id=event_id,
                user_id=user_id,
                aggregate_id=aggregate_id,
                aggregate_type="PROFILE",
                version=version,
                kind=kind,
                payload={} if payload is None else payload,
            )
        )
        return event_id


def _roadmap_outcomes(raw_outcomes: Sequence[JsonValue]) -> tuple[RoadmapOutcome, ...]:
    return tuple(RoadmapOutcome.model_validate(raw_outcome) for raw_outcome in raw_outcomes)


def _outcome_semantics(
    raw_outcomes: Sequence[JsonValue],
) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    return tuple(
        (
            outcome.requirement_key,
            outcome.entity_id,
            outcome.raw_text,
            outcome.experience_codes,
        )
        for outcome in _roadmap_outcomes(raw_outcomes)
    )

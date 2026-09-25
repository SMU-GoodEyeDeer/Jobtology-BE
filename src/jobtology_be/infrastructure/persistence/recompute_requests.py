from uuid import uuid4

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.infrastructure.persistence.contracts import (
    AnalysisRecomputeSubmission,
    MissingRecordError,
    PersistenceConflictError,
    RecomputeRequestCreate,
    RecomputeRequestSnapshot,
)
from jobtology_be.infrastructure.persistence.outbox import OutboxRepository
from jobtology_be.infrastructure.persistence.schema import (
    goals,
    profiles,
    user_state_events,
)


class RecomputeRequestRepository(OutboxRepository):
    async def create_recompute_request(
        self, request: RecomputeRequestCreate
    ) -> RecomputeRequestSnapshot:
        async with self._database.sessions.begin() as session:
            trigger_event = await session.execute(
                select(user_state_events.c.user_id, user_state_events.c.version).where(
                    user_state_events.c.id == request.trigger_event_id
                )
            )
            trigger_event_row = trigger_event.one_or_none()
            if trigger_event_row is None or trigger_event_row.user_id != request.user_id:
                raise MissingRecordError(resource="trigger event")
            if trigger_event_row.version != request.profile_version:
                raise PersistenceConflictError(resource="trigger event version")
            return await self._persist_recompute_request(session, request)

    async def submit_analysis_recompute(
        self, submission: AnalysisRecomputeSubmission
    ) -> RecomputeRequestSnapshot:
        async with self._database.sessions.begin() as session:
            return await self.submit_analysis_recompute_in_session(session, submission)

    async def submit_analysis_recompute_in_session(
        self, session: AsyncSession, submission: AnalysisRecomputeSubmission
    ) -> RecomputeRequestSnapshot:
        profile_version = await session.scalar(
            select(profiles.c.version)
            .where(profiles.c.user_id == submission.user_id)
            .with_for_update()
        )
        if profile_version != submission.expected_profile_version:
            raise PersistenceConflictError(resource="profile")
        goal_owner = await session.scalar(select(goals.c.user_id).where(goals.c.id == submission.goal_id))
        if goal_owner != submission.user_id:
            raise MissingRecordError(resource="goal")
        trigger_event_id = uuid4()
        await session.execute(
            insert(user_state_events).values(
                id=trigger_event_id,
                user_id=submission.user_id,
                aggregate_id=trigger_event_id,
                aggregate_type="PROFILE",
                version=submission.expected_profile_version,
                kind="ANALYSIS_REQUESTED",
                payload={"goal_id": str(submission.goal_id)},
            )
        )
        return await self._persist_recompute_request(
            session,
            RecomputeRequestCreate(
                user_id=submission.user_id,
                profile_version=submission.expected_profile_version,
                trigger_event_id=trigger_event_id,
                dedupe_key=submission.dedupe_key,
                payload=submission.payload,
                context=submission.context,
            ),
        )

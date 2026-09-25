from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from jobtology_be.contracts import RoadmapOutcome
from jobtology_be.infrastructure.persistence.completion_outcomes import (
    canonical_completion_outcomes,
)
from jobtology_be.infrastructure.persistence.configured_recompute import (
    ConfiguredRecomputeRepository,
)
from jobtology_be.infrastructure.persistence.contracts import (
    JsonValue,
    MissingRecordError,
    PersistenceConflictError,
    RoadmapSnapshot,
    StepStateMutation,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.outbox import OutboxRepository
from jobtology_be.infrastructure.persistence.schema import (
    profiles,
    roadmap_steps,
    roadmaps,
    step_completion_inheritances,
    user_capabilities,
    user_state_events,
)


class RoadmapStepRepository(OutboxRepository):
    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self._recompute = ConfiguredRecomputeRepository(database)

    async def mutate_step_state(self, request: StepStateMutation) -> RoadmapSnapshot:
        async with self._database.sessions.begin() as session:
            await self._bump_profile(session, request.user_id, request.expected_profile_version)
            roadmap = await session.execute(
                select(roadmaps.c.state, roadmaps.c.goal_id)
                .where(
                    roadmaps.c.id == request.roadmap_id,
                    roadmaps.c.user_id == request.user_id,
                    roadmaps.c.version == request.expected_roadmap_version,
                )
                .with_for_update()
            )
            roadmap_row = roadmap.one_or_none()
            if roadmap_row is None or roadmap_row.state != "ACTIVE":
                raise PersistenceConflictError(resource="active roadmap")
            step = await session.execute(
                select(roadmap_steps.c.state, roadmap_steps.c.outcomes).where(
                    roadmap_steps.c.id == request.step_id,
                    roadmap_steps.c.roadmap_id == request.roadmap_id,
                )
            )
            step_row = step.one_or_none()
            if step_row is None:
                raise MissingRecordError(resource="roadmap step")
            valid_transition = (step_row.state, request.state) in {
                ("TODO", "IN_PROGRESS"),
                ("IN_PROGRESS", "TODO"),
                ("IN_PROGRESS", "COMPLETED"),
                ("COMPLETED", "IN_PROGRESS"),
            }
            if not valid_transition:
                raise PersistenceConflictError(resource="step state transition")
            result = await session.execute(
                update(roadmap_steps)
                .where(
                    roadmap_steps.c.id == request.step_id,
                    roadmap_steps.c.roadmap_id == request.roadmap_id,
                )
                .values(state=request.state, updated_at=datetime.now(UTC))
            )
            if result.rowcount != 1:
                raise MissingRecordError(resource="roadmap step")
            await session.execute(
                update(roadmaps)
                .where(roadmaps.c.id == request.roadmap_id)
                .values(version=roadmaps.c.version + 1, updated_at=datetime.now(UTC))
            )
            event_kind = "STEP_COMPLETED" if request.state == "COMPLETED" else "STEP_STATE_UPDATED"
            if step_row.state == "COMPLETED" and request.state == "IN_PROGRESS":
                completion_event_id = await session.scalar(
                    select(step_completion_inheritances.c.original_completion_event_id).where(
                        step_completion_inheritances.c.step_id == request.step_id
                    )
                )
                if completion_event_id is None:
                    raise PersistenceConflictError(resource="step completion lineage")
                await session.execute(
                    update(user_capabilities)
                    .where(user_capabilities.c.source_completion_event_id == completion_event_id)
                    .values(lifecycle="REVOKED", updated_at=datetime.now(UTC))
                )
                event_kind = "STEP_COMPLETION_REVERSED"
            event_id = await self._append_event(
                session,
                request.user_id,
                request.step_id,
                request.expected_profile_version + 1,
                event_kind,
                {"outcomes": step_row.outcomes},
            )
            if request.state == "COMPLETED":
                await session.execute(
                    postgres_insert(step_completion_inheritances)
                    .values(
                        step_id=request.step_id,
                        original_completion_event_id=event_id,
                    )
                    .on_conflict_do_update(
                        index_elements=[step_completion_inheritances.c.step_id],
                        set_={"original_completion_event_id": event_id},
                    )
                )
                for outcome in canonical_completion_outcomes(_roadmap_outcomes(step_row.outcomes)):
                    await session.execute(
                        postgres_insert(user_capabilities)
                        .values(
                            id=uuid4(),
                            user_id=request.user_id,
                            category="ROADMAP_OUTCOME",
                            raw_text=outcome.raw_text,
                            entity_id=outcome.entity_id,
                            verification="SELF_REPORTED",
                            lifecycle="ACTIVE",
                            details={
                                "outcome_requirement_keys": list(outcome.requirement_keys),
                                "experience_codes": list(outcome.experience_codes),
                                "outcome_provenance": [
                                    {
                                        "requirement_key": item.requirement_key,
                                        "raw_text": item.raw_text,
                                        "experience_codes": list(item.experience_codes),
                                    }
                                    for item in outcome.provenance
                                ],
                                "step_id": str(request.step_id),
                            },
                            source_completion_event_id=event_id,
                        )
                        .on_conflict_do_nothing(
                            constraint="user_capabilities_completion_entity_key"
                        )
                    )
            if event_kind in {"STEP_COMPLETED", "STEP_COMPLETION_REVERSED"}:
                await self._recompute.enqueue_for_goal(
                    session,
                    request.user_id,
                    request.expected_profile_version + 1,
                    event_id,
                    roadmap_row.goal_id,
                )
            else:
                await self._enqueue_recompute(
                    session, request.user_id, request.expected_profile_version + 1, event_id
                )
        return RoadmapSnapshot(
            roadmap_id=request.roadmap_id,
            version=request.expected_roadmap_version + 1,
            state="ACTIVE",
        )

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

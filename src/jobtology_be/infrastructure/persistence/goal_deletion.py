"""Hard-delete one owned goal while retaining account-wide skills and audit history."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, insert, or_, select, update

from jobtology_be.infrastructure.persistence import schema as s
from jobtology_be.infrastructure.persistence.configured_recompute import (
    ConfiguredRecomputeRepository,
)
from jobtology_be.infrastructure.persistence.contracts import (
    GoalDelete,
    MissingRecordError,
    PersistenceConflictError,
    ProfileSnapshot,
)
from jobtology_be.infrastructure.persistence.database import Database


class GoalDeletionRepository:
    def __init__(self, database: Database, *, capability_list_authoritative: bool = False) -> None:
        self._database = database
        self._recomputes = ConfiguredRecomputeRepository(
            database, capability_list_authoritative=capability_list_authoritative,
        )

    async def delete_goal(self, request: GoalDelete) -> ProfileSnapshot:
        async with self._database.sessions.begin() as session:
            # Match account deletion/worker ownership locks while allowing FK key
            # checks from a write already holding the profile lock to finish.
            await session.scalar(select(s.users.c.id).where(
                s.users.c.id == request.user_id,
            ).with_for_update(key_share=True))
            profile = (await session.execute(select(
                s.profiles.c.version, s.profiles.c.latest_analysis_id,
            ).where(s.profiles.c.user_id == request.user_id).with_for_update())).one_or_none()
            owned = await session.scalar(select(s.goals.c.id).where(
                s.goals.c.id == request.goal_id, s.goals.c.user_id == request.user_id,
            ).with_for_update())
            if owned is None:
                raise MissingRecordError(resource="goal")
            if profile is None or profile.version != request.expected_profile_version:
                raise PersistenceConflictError(resource="profile")

            analysis_ids = tuple(await session.scalars(select(s.analyses.c.id).where(
                s.analyses.c.goal_id == request.goal_id, s.analyses.c.user_id == request.user_id,
            )))
            proposal_rows = (await session.execute(select(
                s.route_proposals.c.id, s.route_proposals.c.decision_trace_id,
            ).where(s.route_proposals.c.analysis_id.in_(analysis_ids),
                    s.route_proposals.c.user_id == request.user_id))).all()
            proposal_ids = tuple(row.id for row in proposal_rows)
            trace_ids = tuple(row.decision_trace_id for row in proposal_rows)
            roadmap_ids = tuple(await session.scalars(select(s.roadmaps.c.id).where(
                s.roadmaps.c.goal_id == request.goal_id, s.roadmaps.c.user_id == request.user_id,
            )))
            step_ids = tuple(await session.scalars(select(s.roadmap_steps.c.id).where(
                s.roadmap_steps.c.roadmap_id.in_(roadmap_ids),
            )))
            context_goal = s.recompute_contexts.c.payload["goal_id"].astext
            goal_events = select(s.user_state_events.c.id).where(
                s.user_state_events.c.user_id == request.user_id,
                or_(
                    s.user_state_events.c.aggregate_id == request.goal_id,
                    s.user_state_events.c.aggregate_id.in_(step_ids),
                    s.user_state_events.c.payload["goal_id"].astext == str(request.goal_id),
                ),
            )
            bound_contexts = select(s.recompute_contexts.c.request_id).where(context_goal.is_not(None))
            request_ids = tuple(await session.scalars(select(s.recompute_requests.c.id).where(
                s.recompute_requests.c.user_id == request.user_id,
                or_(
                    s.recompute_requests.c.resulting_analysis_id.in_(analysis_ids),
                    s.recompute_requests.c.proposal_id.in_(proposal_ids),
                    s.recompute_requests.c.id.in_(select(s.recompute_contexts.c.request_id).where(
                        context_goal == str(request.goal_id),
                    )),
                    # Draft edits can trigger a recompute for another active
                    # goal. Its explicit context takes precedence over its event.
                    (~s.recompute_requests.c.id.in_(bound_contexts))
                    & s.recompute_requests.c.trigger_event_id.in_(goal_events),
                ),
            )))

            # Materialized IDs keep predicates valid after their parent rows go.
            # Pending/running jobs are removed before requests; a late finalizer
            # then observes a lost lease rather than recreating deleted results.
            for table, predicate in (
                (s.step_completion_inheritances, s.step_completion_inheritances.c.step_id.in_(step_ids)),
                (s.step_dependencies, s.step_dependencies.c.roadmap_id.in_(roadmap_ids)),
                (s.roadmap_plan_overrides, s.roadmap_plan_overrides.c.roadmap_id.in_(roadmap_ids)),
                (s.roadmap_steps, s.roadmap_steps.c.roadmap_id.in_(roadmap_ids)),
                (s.roadmaps, s.roadmaps.c.id.in_(roadmap_ids)),
                (s.outbox_jobs, or_(
                    s.outbox_jobs.c.recompute_request_id.in_(request_ids),
                    s.outbox_jobs.c.recompute_request_id.is_(None)
                    & (s.outbox_jobs.c.kind == "RECOMPUTE")
                    & s.outbox_jobs.c.payload["recompute_request_id"].astext.in_(
                        tuple(str(value) for value in request_ids),
                    ),
                )),
                (s.recompute_contexts, s.recompute_contexts.c.request_id.in_(request_ids)),
                (s.recompute_requests, s.recompute_requests.c.id.in_(request_ids)),
                (s.route_proposals, s.route_proposals.c.id.in_(proposal_ids)),
                (s.analyses, s.analyses.c.id.in_(analysis_ids)),
            ):
                await session.execute(delete(table).where(predicate))
            # A legacy trace may be shared by another proposal; retain it then.
            deleted_trace_ids = tuple(await session.scalars(delete(s.calculation_traces).where(
                s.calculation_traces.c.id.in_(trace_ids),
                s.calculation_traces.c.user_id == request.user_id,
                ~select(s.route_proposals.c.id).where(
                    s.route_proposals.c.decision_trace_id == s.calculation_traces.c.id,
                ).exists(),
            ).returning(s.calculation_traces.c.id)))
            references = (
                ("goal_id", (request.goal_id,)), ("analysis_id", analysis_ids),
                ("resulting_analysis_id", analysis_ids), ("proposal_id", proposal_ids),
                ("decision_trace_id", deleted_trace_ids), ("roadmap_id", roadmap_ids),
                ("recompute_request_id", request_ids),
            )
            await session.execute(delete(s.idempotency_records).where(
                s.idempotency_records.c.user_id == request.user_id,
                or_(*(
                    s.idempotency_records.c.response[field].astext.in_(tuple(str(value) for value in ids))
                    for field, ids in references
                )),
            ))
            await session.execute(delete(s.goals).where(s.goals.c.id == request.goal_id))
            next_version = request.expected_profile_version + 1
            await session.execute(update(s.profiles).where(s.profiles.c.user_id == request.user_id).values(
                version=next_version, updated_at=datetime.now(UTC),
                latest_analysis_id=(
                    None if profile.latest_analysis_id in analysis_ids else profile.latest_analysis_id
                ),
            ))
            event_id = uuid4()
            await session.execute(insert(s.user_state_events).values(
                id=event_id, user_id=request.user_id, aggregate_id=request.goal_id,
                aggregate_type="PROFILE", version=next_version, kind="GOAL_DELETED", payload={},
            ))
            active_goal = await session.scalar(select(s.goals.c.id).where(
                s.goals.c.user_id == request.user_id, s.goals.c.status == "ACTIVE",
            ))
            if active_goal is not None:
                await self._recomputes.enqueue_for_goal(
                    session, request.user_id, next_version, event_id, active_goal,
                )
        return ProfileSnapshot(user_id=request.user_id, version=next_version)

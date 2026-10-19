"""Delete one authenticated account's complete application data atomically."""

from uuid import UUID

from sqlalchemy import delete, select

from jobtology_be.infrastructure.persistence import schema as s
from jobtology_be.infrastructure.persistence.database import Database


class PostgresAccountService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def delete_account(self, user_id: UUID) -> None:
        async with self._database.sessions.begin() as session:
            # NO KEY UPDATE serializes account-level work while allowing FK key
            # checks by a write already holding the profile lock to finish.
            existing = await session.scalar(
                select(s.users.c.id).where(s.users.c.id == user_id).with_for_update(key_share=True)
            )
            if existing is None:
                return
            await session.execute(
                select(s.profiles.c.user_id)
                .where(s.profiles.c.user_id == user_id)
                .with_for_update()
            )
            roadmap_ids = select(s.roadmaps.c.id).where(s.roadmaps.c.user_id == user_id)
            step_ids = select(s.roadmap_steps.c.id).where(
                s.roadmap_steps.c.roadmap_id.in_(roadmap_ids)
            )
            request_ids = select(s.recompute_requests.c.id).where(
                s.recompute_requests.c.user_id == user_id
            )
            # Foreign keys are deliberately restrictive; remove descendants first.
            for table, predicate in (
                (
                    s.step_completion_inheritances,
                    s.step_completion_inheritances.c.step_id.in_(step_ids),
                ),
                (s.step_dependencies, s.step_dependencies.c.roadmap_id.in_(roadmap_ids)),
                (s.roadmap_plan_overrides, s.roadmap_plan_overrides.c.roadmap_id.in_(roadmap_ids)),
                (s.roadmap_steps, s.roadmap_steps.c.roadmap_id.in_(roadmap_ids)),
                (s.roadmaps, s.roadmaps.c.user_id == user_id),
                (s.outbox_jobs, s.outbox_jobs.c.recompute_request_id.in_(request_ids)),
                (s.recompute_contexts, s.recompute_contexts.c.request_id.in_(request_ids)),
                (s.recompute_requests, s.recompute_requests.c.user_id == user_id),
                (s.route_proposals, s.route_proposals.c.user_id == user_id),
                (s.calculation_traces, s.calculation_traces.c.user_id == user_id),
                (s.analyses, s.analyses.c.user_id == user_id),
                (s.user_capabilities, s.user_capabilities.c.user_id == user_id),
                (s.route_preferences, s.route_preferences.c.user_id == user_id),
                (s.idempotency_records, s.idempotency_records.c.user_id == user_id),
                (s.goals, s.goals.c.user_id == user_id),
                (s.user_state_events, s.user_state_events.c.user_id == user_id),
                (s.auth_sessions, s.auth_sessions.c.user_id == user_id),
                (s.account_rate_limits, s.account_rate_limits.c.user_id == user_id),
                (s.profiles, s.profiles.c.user_id == user_id),
                (s.google_identities, s.google_identities.c.user_id == user_id),
                (s.users, s.users.c.id == user_id),
            ):
                await session.execute(delete(table).where(predicate))

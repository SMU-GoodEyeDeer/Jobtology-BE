"""Real PostgreSQL regressions for schedules and durable automatic roadmap work."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import anyio
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from test_acceptance_m5_worker import WorkerPublication, enqueue_recompute, process_recompute

from jobtology_be.application.services.roadmaps import PersistentRoadmapService
from jobtology_be.infrastructure.persistence.contracts import (
    GoalMutation,
    ProfileMutation,
    RoadmapCreate,
    UserCreate,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    profiles,
    recompute_contexts,
    recompute_requests,
    roadmap_steps,
    roadmaps,
    route_proposals,
)
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.product_roles.worker import InProcessRecomputeLoop

pytest_plugins = ("test_acceptance_persistence",)


class IdleWorker:
    async def process_once(self, *, limit: int) -> tuple:
        return ()


class RoleNames:
    def display_name(self, occupation_id: str) -> str:
        return "Backend developer"


def _loop(database: Database) -> InProcessRecomputeLoop:
    return InProcessRecomputeLoop(
        worker=IdleWorker(), database=database,
        roadmap_service=PersistentRoadmapService(PostgresApplicationStore(database)),
        holder=RoleNames(),
    )


async def _create_goal(database: Database) -> tuple[UUID, UUID]:
    user_id = uuid4()
    store = PostgresApplicationStore(database)
    await store.create_user(UserCreate(user_id=user_id))
    goal = await store.mutate_goal(GoalMutation(
        user_id=user_id, expected_profile_version=1, goal_mode="TARGETED",
        occupation_id="BACKEND_DEVELOPER", target_by=datetime.now(UTC) + timedelta(days=30),
        timezone="UTC", original_time_phrase="in one month", status="ACTIVE", goal_id=None,
    ))
    return user_id, goal.goal_id


async def _ready_proposal(database: Database) -> tuple[UUID, UUID, WorkerPublication]:
    user_id, goal_id = await _create_goal(database)
    publication = await process_recompute(
        # str(URL) masks passwords and cannot be reused as a connection string.
        database.engine.url.render_as_string(hide_password=False), user_id, 2, goal_id,
        target_by=datetime.now(UTC) + timedelta(days=30),
    )
    return user_id, goal_id, publication


def test_restart_reconciles_committed_analysis_and_preserves_solver_dates(
    acceptance_database_url: str,
) -> None:
    async def exercise() -> None:
        database = Database.create(acceptance_database_url)
        try:
            user_id, _, publication = await _ready_proposal(database)
            async with database.sessions() as session:
                assert await session.scalar(select(recompute_requests.c.auto_roadmap_pending).where(
                    recompute_requests.c.id == publication.recompute_request_id,
                )) is True
                assert await session.scalar(select(func.count()).select_from(roadmaps)) == 0
                original = await session.scalar(select(route_proposals.c.steps).where(
                    route_proposals.c.id == publication.proposal_id,
                ))
            # The worker's return value was lost by a restart; a fresh loop still finds its work.
            await _loop(database).process_once()
            await _loop(database).process_once()
            async with database.sessions() as session:
                rows = (await session.execute(select(
                    roadmaps.c.state, roadmap_steps.c.planned_start, roadmap_steps.c.planned_end,
                ).join(roadmap_steps).where(roadmaps.c.user_id == user_id))).all()
                assert await session.scalar(select(recompute_requests.c.auto_roadmap_pending).where(
                    recompute_requests.c.id == publication.recompute_request_id,
                )) is False
            assert len(rows) == 1
            assert rows[0].state == "ACTIVE"
            assert rows[0].planned_start == datetime.fromisoformat(original[0]["planned_start_at"])
            assert rows[0].planned_end == datetime.fromisoformat(original[0]["planned_end_at"])
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_activation_sql_failure_rolls_back_creation_and_retries_after_restart(
    acceptance_database_url: str,
) -> None:
    async def exercise() -> None:
        database = Database.create(acceptance_database_url)
        try:
            _, _, publication = await _ready_proposal(database)
            async with database.sessions.begin() as session:
                await session.execute(text("""
                    CREATE FUNCTION reject_activation() RETURNS trigger AS $$
                    BEGIN RAISE EXCEPTION 'transient activation failure'; END;
                    $$ LANGUAGE plpgsql
                """))
                await session.execute(text("""
                    CREATE TRIGGER reject_activation BEFORE UPDATE ON roadmaps
                    FOR EACH ROW WHEN (NEW.state = 'ACTIVE') EXECUTE FUNCTION reject_activation()
                """))
            # The supervisor must see the incomplete cycle so its readiness
            # heartbeat cannot mark a failed activation as successful.
            with pytest.raises(DBAPIError, match="transient activation failure"):
                await _loop(database).process_once()
            async with database.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(roadmaps)) == 0
                assert await session.scalar(select(recompute_requests.c.auto_roadmap_pending).where(
                    recompute_requests.c.id == publication.recompute_request_id,
                )) is True
            async with database.sessions.begin() as session:
                await session.execute(text("DROP TRIGGER reject_activation ON roadmaps"))
            await _loop(database).process_once()
            async with database.sessions() as session:
                assert (await session.execute(select(roadmaps.c.state))).scalars().all() == ["ACTIVE"]
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_two_reconcilers_create_and_activate_exactly_one_roadmap(
    acceptance_database_url: str,
) -> None:
    async def exercise() -> None:
        database = Database.create(acceptance_database_url)
        try:
            await _ready_proposal(database)
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(_loop(database).process_once)
                tasks.start_soon(_loop(database).process_once)
            async with database.sessions() as session:
                assert (await session.execute(select(roadmaps.c.state))).scalars().all() == ["ACTIVE"]
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_reconciler_recovers_a_legacy_draft_without_duplicating_it(
    acceptance_database_url: str,
) -> None:
    async def exercise() -> None:
        database = Database.create(acceptance_database_url)
        try:
            user_id, goal_id, publication = await _ready_proposal(database)
            draft = await PostgresApplicationStore(database).create_roadmap(RoadmapCreate(
                user_id=user_id, roadmap_id=uuid4(), goal_id=goal_id,
                proposal_id=publication.proposal_id, title="Keep this title", profile_version=2,
            ))
            await _loop(database).process_once()
            async with database.sessions() as session:
                assert (await session.execute(select(
                    roadmaps.c.id, roadmaps.c.state, roadmaps.c.title,
                ))).all() == [(draft.roadmap_id, "ACTIVE", "Keep this title")]
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_input_edit_carries_first_running_context_and_does_not_publish_stale_pointer(
    acceptance_database_url: str,
) -> None:
    async def exercise() -> None:
        database = Database.create(acceptance_database_url)
        try:
            user_id, goal_id = await _create_goal(database)
            request_id = await enqueue_recompute(
                acceptance_database_url, user_id, 2, goal_id,
                target_by=datetime.now(UTC) + timedelta(days=30),
            )
            store = PostgresApplicationStore(database, capability_list_authoritative=True)
            work = await store.claim_recompute_work(limit=1)
            assert work[0].request_id == request_id
            # First analysis has no READY/PENDING predecessor: only the RUNNING context exists.
            await store.mutate_profile(ProfileMutation(
                user_id=user_id, expected_version=2, major_raw="Computer science",
                major_concept_id=None, year=None, enrollment_status=None, expected_graduation_on=None,
            ))
            async with database.sessions() as session:
                new = (await session.execute(select(
                    recompute_requests.c.state, recompute_requests.c.error_code,
                    recompute_contexts.c.payload,
                ).join(recompute_contexts).where(
                    recompute_requests.c.user_id == user_id,
                    recompute_requests.c.profile_version == 3,
                ))).one()
                assert new.state == "PENDING" and new.error_code is None
                assert new.payload["profile_version"] == 3
                assert new.payload["goal_id"] == str(goal_id)
                assert await session.scalar(select(profiles.c.latest_analysis_id).where(
                    profiles.c.user_id == user_id,
                )) is None
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_migration_repairs_only_safe_unambiguous_origin_dates_and_queues_existing_work(
    acceptance_database_url: str,
) -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import insert

    async def seed() -> tuple[UUID, dict[str, UUID], datetime, datetime]:
        database = Database.create(acceptance_database_url)
        try:
            user_id, goal_id, publication = await _ready_proposal(database)
            store = PostgresApplicationStore(database)
            ids = {}
            async with database.sessions() as session:
                proposal = dict((await session.execute(select(route_proposals).where(
                    route_proposals.c.id == publication.proposal_id,
                ))).mappings().one())
            start = datetime.fromisoformat(proposal["steps"][0]["planned_start_at"])
            end = datetime.fromisoformat(proposal["steps"][0]["planned_end_at"])
            for kind in ("repair", "preserve", "unmatched", "invalid", "ambiguous", "partial"):
                created = await store.create_roadmap(RoadmapCreate(
                    user_id=user_id, roadmap_id=uuid4(), goal_id=goal_id,
                    proposal_id=publication.proposal_id, title=kind, profile_version=2,
                ))
                ids[kind] = created.roadmap_id
                async with database.sessions.begin() as session:
                    await session.execute(update(roadmap_steps).where(
                        roadmap_steps.c.roadmap_id == created.roadmap_id,
                    ).values(planned_start=None, planned_end=None, state="IN_PROGRESS"))
                    if kind == "preserve":
                        await session.execute(update(roadmap_steps).where(
                            roadmap_steps.c.roadmap_id == created.roadmap_id,
                        ).values(planned_start=start + timedelta(days=1), planned_end=end + timedelta(days=1)))
                    elif kind == "partial":
                        await session.execute(update(roadmap_steps).where(
                            roadmap_steps.c.roadmap_id == created.roadmap_id,
                        ).values(planned_start=start - timedelta(hours=1)))
                    elif kind == "unmatched":
                        await session.execute(update(roadmap_steps).where(
                            roadmap_steps.c.roadmap_id == created.roadmap_id,
                        ).values(step_key="different-step"))
                    elif kind in {"invalid", "ambiguous"}:
                        copied = dict(proposal)
                        copied["id"] = uuid4()
                        copied["steps"] = [dict(step) for step in proposal["steps"]]
                        if kind == "invalid":
                            copied["steps"][0]["planned_start_at"] = "not a timestamp"
                        else:
                            copied["steps"] *= 2
                        await session.execute(insert(route_proposals).values(**copied))
                        await session.execute(update(roadmaps).where(
                            roadmaps.c.id == created.roadmap_id,
                        ).values(proposal_id=copied["id"]))
            return publication.recompute_request_id, ids, start, end
        finally:
            await database.dispose()

    request_id, ids, original_start, original_end = anyio.run(seed)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    config.set_main_option("sqlalchemy.url", acceptance_database_url)
    command.downgrade(config, "20261019_01")
    command.upgrade(config, "head")

    async def verify() -> None:
        database = Database.create(acceptance_database_url)
        try:
            async with database.sessions() as session:
                rows = (await session.execute(select(
                    roadmap_steps.c.roadmap_id, roadmap_steps.c.planned_start,
                    roadmap_steps.c.planned_end, roadmap_steps.c.state,
                ))).all()
                assert await session.scalar(select(recompute_requests.c.auto_roadmap_pending).where(
                    recompute_requests.c.id == request_id,
                )) is True
            by_roadmap = {row.roadmap_id: row for row in rows}
            assert by_roadmap[ids["repair"]][1:3] == (original_start, original_end)
            assert by_roadmap[ids["preserve"]][1:3] == (
                original_start + timedelta(days=1), original_end + timedelta(days=1),
            )
            assert by_roadmap[ids["partial"]][1:3] == (
                original_start - timedelta(hours=1), original_end,
            )
            for kind in ("unmatched", "invalid", "ambiguous"):
                assert by_roadmap[ids[kind]][1:3] == (None, None)
            assert {row.state for row in rows} == {"IN_PROGRESS"}
        finally:
            await database.dispose()
    anyio.run(verify)


def test_google_only_writes_wait_for_account_erasure_and_never_resurrect_users(
    acceptance_database_url: str,
) -> None:
    import pytest
    from sqlalchemy import delete, insert

    from jobtology_be.application.services.profiles import (
        PersistentProfileService,
        ProfileUpdateCommand,
    )
    from jobtology_be.infrastructure.persistence.contracts import MissingRecordError
    from jobtology_be.infrastructure.persistence.schema import google_identities, users

    async def exercise() -> None:
        database = Database.create(acceptance_database_url)
        user_id = uuid4()
        strict_store = PostgresApplicationStore(database, google_accounts_only=True)
        try:
            # Ordinary writes cannot create a Google account or anonymous profile.
            with pytest.raises(MissingRecordError):
                await strict_store.create_user(UserCreate(user_id=user_id))
            await PostgresApplicationStore(database).create_user(UserCreate(user_id=user_id))
            with pytest.raises(MissingRecordError):
                await strict_store.create_user(UserCreate(user_id=user_id))
            async with database.sessions.begin() as session:
                await session.execute(insert(google_identities).values(
                    issuer="https://accounts.google.com", subject=f"subject-{user_id}", user_id=user_id,
                ))
            assert (await strict_store.create_user(UserCreate(user_id=user_id))).version == 1
            deleting = anyio.Event()
            writer_started = anyio.Event()
            errors = []

            async def erase() -> None:
                async with database.sessions.begin() as session:
                    await session.scalar(select(users.c.id).where(users.c.id == user_id)
                                         .with_for_update(key_share=True))
                    await session.scalar(select(profiles.c.version).where(profiles.c.user_id == user_id)
                                         .with_for_update())
                    deleting.set()
                    await writer_started.wait()
                    await session.execute(delete(google_identities).where(google_identities.c.user_id == user_id))
                    await session.execute(delete(profiles).where(profiles.c.user_id == user_id))
                    await session.execute(delete(users).where(users.c.id == user_id))

            async def stale_authenticated_write() -> None:
                await deleting.wait()
                writer_started.set()
                try:
                    await PersistentProfileService(strict_store).update_profile(user_id, ProfileUpdateCommand(
                        expected_profile_version=1, major_raw="Old authenticated request",
                        major_concept_id=None, year=None, enrollment_status=None, expected_graduation_on=None,
                    ))
                except MissingRecordError as error:
                    errors.append(error)

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(erase)
                tasks.start_soon(stale_authenticated_write)
            assert len(errors) == 1
            async with database.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(users)) == 0
                assert await session.scalar(select(func.count()).select_from(profiles)) == 0
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_expired_legacy_source_is_settled_and_other_accounts_still_progress(
    acceptance_database_url: str,
) -> None:
    async def exercise() -> None:
        database = Database.create(acceptance_database_url)
        try:
            user_id, goal_id, publication = await _ready_proposal(database)
            draft = await PostgresApplicationStore(database).create_roadmap(RoadmapCreate(
                user_id=user_id, roadmap_id=uuid4(), goal_id=goal_id,
                proposal_id=publication.proposal_id, title="Expired source", profile_version=2,
            ))
            async with database.sessions.begin() as session:
                await session.execute(update(roadmaps).where(roadmaps.c.id == draft.roadmap_id).values(
                    validity={"expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
                ))
            other_user_id, _, _ = await _ready_proposal(database)
            await _loop(database).process_once()
            async with database.sessions() as session:
                assert await session.scalar(select(recompute_requests.c.auto_roadmap_pending).where(
                    recompute_requests.c.id == publication.recompute_request_id,
                )) is False
                assert await session.scalar(select(roadmaps.c.state).where(
                    roadmaps.c.id == draft.roadmap_id,
                )) == "DRAFT"
                assert await session.scalar(select(roadmaps.c.state).where(
                    roadmaps.c.user_id == other_user_id,
                )) == "ACTIVE"
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_permanent_activation_conflict_rolls_back_partial_roadmap_and_settles_work(
    acceptance_database_url: str,
) -> None:
    from jobtology_be.infrastructure.persistence.contracts import PersistenceConflictError

    class ExpiringSourceStore(PostgresApplicationStore):
        async def mutate_roadmap(self, request):
            raise PersistenceConflictError(resource="roadmap source")

    async def exercise() -> None:
        database = Database.create(acceptance_database_url)
        try:
            _, _, publication = await _ready_proposal(database)
            loop = InProcessRecomputeLoop(
                worker=IdleWorker(), database=database,
                roadmap_service=PersistentRoadmapService(ExpiringSourceStore(database)),
                holder=RoleNames(),
            )
            await loop.process_once()
            async with database.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(roadmaps)) == 0
                assert await session.scalar(select(recompute_requests.c.auto_roadmap_pending).where(
                    recompute_requests.c.id == publication.recompute_request_id,
                )) is False
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_idempotent_write_and_account_erasure_share_user_first_lock_order(
    acceptance_database_url: str,
) -> None:
    import pytest
    from sqlalchemy import insert

    from jobtology_be.infrastructure.persistence.accounts import PostgresAccountService
    from jobtology_be.infrastructure.persistence.contracts import (
        IdempotencyAcquire,
        MissingRecordError,
    )
    from jobtology_be.infrastructure.persistence.schema import (
        google_identities,
        idempotency_records,
        users,
    )

    async def exercise() -> None:
        database = Database.create(acceptance_database_url)
        user_id = uuid4()
        try:
            await PostgresApplicationStore(database).create_user(UserCreate(user_id=user_id))
            async with database.sessions.begin() as session:
                await session.execute(insert(google_identities).values(
                    issuer="https://accounts.google.com", subject=f"subject-{user_id}", user_id=user_id,
                ))
            store = PostgresApplicationStore(database, google_accounts_only=True)
            writing = anyio.Event()
            deleting = anyio.Event()
            request = IdempotencyAcquire(
                user_id=user_id, method="POST", path="/api/v1/me/goals", key="race",
                request_hash="payload", expires_at=datetime.now(UTC) + timedelta(days=1),
            )

            async def operation():
                writing.set()
                await deleting.wait()
                goal = await store.mutate_goal(GoalMutation(
                    user_id=user_id, expected_profile_version=1, goal_mode="TARGETED",
                    occupation_id="BACKEND_DEVELOPER", target_by=datetime.now(UTC) + timedelta(days=30),
                    timezone="UTC", original_time_phrase="one month", status="ACTIVE", goal_id=None,
                ))
                return {"goal_id": str(goal.goal_id)}

            async def writer():
                await store.execute_idempotency(request, 201, operation)

            async def eraser():
                await writing.wait()
                deleting.set()
                await PostgresAccountService(database).delete_account(user_id)

            with anyio.fail_after(5):
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(writer)
                    tasks.start_soon(eraser)
            # A stale retry must fail before re-creating an idempotency or account row.
            with pytest.raises(MissingRecordError):
                await store.execute_idempotency(request, 201, operation)
            async with database.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(users)) == 0
                assert await session.scalar(select(func.count()).select_from(idempotency_records)) == 0
        finally:
            await database.dispose()
    anyio.run(exercise)

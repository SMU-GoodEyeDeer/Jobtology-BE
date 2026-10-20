from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from os import environ
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import anyio
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from jobtology_be.infrastructure.persistence.contracts import (
    AnalysisRecomputeSubmission,
    JsonValue,
    UserCreate,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import goals
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore

ACCEPTANCE_DATABASE_URL_ENV: Final = "JOBTOLOGY_ACCEPTANCE_DATABASE_URL"
REFERENCE_AT: Final = datetime(2026, 9, 22, 12, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class IsolatedDatabase:
    admin_url: str
    database_url: str
    database_name: str


def _base_url() -> str:
    database_url = environ.get(ACCEPTANCE_DATABASE_URL_ENV)
    if database_url is None:
        pytest.skip(f"Set {ACCEPTANCE_DATABASE_URL_ENV} to run PostgreSQL acceptance tests")
    return database_url


async def _provision_database(base_url: str) -> IsolatedDatabase:
    url = make_url(base_url)
    database_name = f"jobtology_analysis_submission_{uuid4().hex}"
    admin_url = url.set(database="postgres").render_as_string(hide_password=False)
    database_url = url.set(database=database_name).render_as_string(hide_password=False)
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        await engine.dispose()
    return IsolatedDatabase(
        admin_url=admin_url,
        database_url=database_url,
        database_name=database_name,
    )


async def _drop_database(database: IsolatedDatabase) -> None:
    engine = create_async_engine(database.admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{database.database_name}" WITH (FORCE)'))
    finally:
        await engine.dispose()


def _upgrade_database(database_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


@pytest.fixture
def analysis_submission_database_url() -> Iterator[str]:
    database = anyio.run(_provision_database, _base_url())
    try:
        _upgrade_database(database.database_url)
        yield database.database_url
    finally:
        anyio.run(_drop_database, database)


def _context_payload(user_id: UUID, goal_id: UUID) -> Mapping[str, JsonValue]:
    return {
        "user_id": str(user_id),
        "profile_version": 1,
        "goal_id": str(goal_id),
        "snapshot_selection": {
            "occupation_id": "BACKEND_DEVELOPER",
            "basis_version": "reviewed-v1",
            "release_id": "release-reviewed-v1",
        },
        "capabilities": [],
        "completeness": {
            "entities_complete": True,
            "experience_complete_entity_ids": [],
        },
        "constraints": {
            "target_by": "2026-10-22T12:00:00+00:00",
            "available_hours_per_week": 4,
        },
        "reference_at": REFERENCE_AT.isoformat(),
        "planning_started_at": REFERENCE_AT.isoformat(),
        "calendar": [],
        "candidate_availability": [],
        "solver_settings": {"time_limit_seconds": 20.0},
    }


async def _exercise_atomic_submission(database_url: str) -> None:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)
    user_id = uuid4()
    goal_id = uuid4()
    try:
        await store.create_user(UserCreate(user_id=user_id))
        async with database.sessions.begin() as session:
            await session.execute(
                insert(goals).values(
                    id=goal_id,
                    user_id=user_id,
                    goal_mode="DISCOVERY",
                    target_by=REFERENCE_AT,
                    timezone="UTC",
                    original_time_phrase="Explore a role",
                    status="ACTIVE",
                )
            )
        submission = AnalysisRecomputeSubmission(
            user_id=user_id,
            goal_id=goal_id,
            expected_profile_version=1,
            dedupe_key="analysis-submission-1",
            payload={"source": "analysis-api"},
            context=_context_payload(user_id, goal_id),
        )

        request = await store.submit_analysis_recompute(submission)

        assert request.state == "PENDING"
        async with database.sessions() as session:
            binding = (
                await session.execute(
                    text(
                        "SELECT event.kind, event.payload, request.trigger_event_id, "
                        "context.payload, job.state, job.payload "
                        "FROM recompute_requests AS request "
                        "JOIN user_state_events AS event ON event.id = request.trigger_event_id "
                        "JOIN recompute_contexts AS context ON context.request_id = request.id "
                        "JOIN outbox_jobs AS job ON job.recompute_request_id = request.id "
                        "WHERE request.id = :request_id"
                    ),
                    {"request_id": request.request_id},
                )
            ).one()
            before_counts = (
                await session.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM user_state_events), "
                        "(SELECT count(*) FROM recompute_requests), "
                        "(SELECT count(*) FROM recompute_contexts), "
                        "(SELECT count(*) FROM outbox_jobs)"
                    )
                )
            ).one()
        assert binding[0] == "ANALYSIS_REQUESTED"
        assert binding[1] == {"goal_id": str(goal_id)}
        assert binding[2] is not None
        assert binding[3] == _context_payload(user_id, goal_id)
        assert binding[4] == "READY"
        assert binding[5] == {
            "recompute_request_id": str(request.request_id),
            "source": "analysis-api",
        }

        repeated = await store.submit_analysis_recompute(
            replace(submission, dedupe_key="analysis-submission-2")
        )
        assert repeated == request

        async with database.sessions() as session:
            after_counts = (
                await session.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM user_state_events), "
                        "(SELECT count(*) FROM recompute_requests), "
                        "(SELECT count(*) FROM recompute_contexts), "
                        "(SELECT count(*) FROM outbox_jobs)"
                    )
                )
            ).one()
        assert after_counts == before_counts

        async with database.sessions.begin() as session:
            await session.execute(
                text("UPDATE recompute_requests SET state = 'FAILED' WHERE id = :id"),
                {"id": request.request_id},
            )
        retried = await store.submit_analysis_recompute(
            replace(submission, dedupe_key="analysis-submission-3")
        )
        assert retried.request_id != request.request_id
        assert retried.state == "PENDING"
        async with database.sessions.begin() as session:
            await session.execute(text("UPDATE recompute_requests SET state = 'READY' WHERE id = :id"),
                                  {"id": retried.request_id})
        reused = await store.submit_analysis_recompute(replace(submission, dedupe_key='reuse-ready'))
        assert reused.request_id == retried.request_id
        refreshed = await store.submit_analysis_recompute(replace(
            submission, dedupe_key='manual-refresh', payload={'source': 'analysis-api', 'refresh': True},
        ))
        assert refreshed.request_id != retried.request_id
        repeated_refresh = await store.submit_analysis_recompute(replace(
            submission, dedupe_key='repeat-manual-refresh', payload={'source': 'analysis-api', 'refresh': True},
        ))
        assert repeated_refresh.request_id == refreshed.request_id
        release_context = dict(submission.context)
        release_context['snapshot_selection'] = dict(release_context['snapshot_selection'], release_id='new-release')
        changed_release = await store.submit_analysis_recompute(replace(
            submission, dedupe_key='new-release', context=release_context,
        ))
        assert changed_release.request_id != refreshed.request_id
        expired_context = dict(release_context, reference_at='2026-09-23T12:00:00+00:00')
        expired = await store.submit_analysis_recompute(replace(
            submission, dedupe_key='next-reference-day', context=expired_context,
        ))
        assert expired.request_id != changed_release.request_id
    finally:
        await database.dispose()


def test_analysis_submission_creates_request_context_and_outbox_atomically(
    analysis_submission_database_url: str,
) -> None:
    # Given
    database_url = analysis_submission_database_url

    # When / Then
    anyio.run(_exercise_atomic_submission, database_url)


def test_new_requests_are_limited_but_matching_work_and_idempotency_are_free(
    analysis_submission_database_url: str,
) -> None:
    from sqlalchemy import func, select

    from jobtology_be.infrastructure.persistence.compute_budget import (
        AnalysisBudgetExceeded,
        AnalysisBudgetPolicy,
    )
    from jobtology_be.infrastructure.persistence.schema import (
        account_rate_limits,
        recompute_requests,
    )

    async def exercise():
        database = Database.create(analysis_submission_database_url)
        store = PostgresApplicationStore(database, analysis_budget_policy=AnalysisBudgetPolicy(
            requests_per_hour=1,
        ))
        owner, goal_id = uuid4(), uuid4()
        try:
            await store.create_user(UserCreate(user_id=owner))
            async with database.sessions.begin() as session:
                await session.execute(insert(goals).values(
                    id=goal_id, user_id=owner, goal_mode='TARGETED', occupation_id='BACKEND_DEVELOPER',
                    target_by=REFERENCE_AT, timezone='UTC', original_time_phrase='target', status='ACTIVE',
                ))
            submission = AnalysisRecomputeSubmission(
                user_id=owner, goal_id=goal_id, expected_profile_version=1,
                dedupe_key='budget-first', payload={'source': 'analysis-api'},
                context=_context_payload(owner, goal_id),
            )
            first = await store.submit_analysis_recompute(submission)
            # A new process/session shares quota, but may still reuse in-flight work.
            restarted = PostgresApplicationStore(database, analysis_budget_policy=AnalysisBudgetPolicy(
                requests_per_hour=1,
            ))
            assert await restarted.submit_analysis_recompute(replace(
                submission, dedupe_key='budget-reuse')) == first
            async with database.sessions.begin() as session:
                await session.execute(text("UPDATE recompute_requests SET state='READY' WHERE id=:id"),
                                      {'id': first.request_id})
            assert (await restarted.submit_analysis_recompute(replace(
                submission, dedupe_key='budget-ready'))).request_id == first.request_id
            with pytest.raises(AnalysisBudgetExceeded):
                await restarted.submit_analysis_recompute(replace(
                    submission, dedupe_key='budget-refresh', payload={'refresh': True},
                ))
            async with database.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(recompute_requests)) == 1
                assert await session.scalar(select(account_rate_limits.c.request_count).where(
                    account_rate_limits.c.user_id == owner,
                    account_rate_limits.c.scope == 'analysis_request_hour')) == 1
        finally:
            await database.dispose()
    anyio.run(exercise)


@pytest.mark.parametrize('erasure_lock', [False, True])
def test_standalone_submission_waits_for_owner_before_locking_profile(
    analysis_submission_database_url: str,
    erasure_lock: bool,
) -> None:
    import asyncio

    from sqlalchemy import select

    from jobtology_be.infrastructure.persistence.schema import profiles, users

    async def exercise():
        database = Database.create(analysis_submission_database_url)
        store = PostgresApplicationStore(database)
        owner, goal_id = uuid4(), uuid4()
        task = None
        try:
            await store.create_user(UserCreate(user_id=owner))
            async with database.sessions.begin() as session:
                await session.execute(insert(goals).values(
                    id=goal_id, user_id=owner, goal_mode='TARGETED', occupation_id='BACKEND_DEVELOPER',
                    target_by=REFERENCE_AT, timezone='UTC', original_time_phrase='target', status='ACTIVE',
                ))
            submission = AnalysisRecomputeSubmission(
                user_id=owner, goal_id=goal_id, expected_profile_version=1,
                dedupe_key='owner-lock-order', payload={}, context=_context_payload(owner, goal_id),
            )
            async with database.sessions.begin() as finalizer:
                await finalizer.scalar(select(users.c.id).where(
                    users.c.id == owner).with_for_update(key_share=erasure_lock))
                if erasure_lock:
                    await finalizer.scalar(select(profiles.c.version).where(
                        profiles.c.user_id == owner).with_for_update())
                task = asyncio.create_task(store.submit_analysis_recompute(submission))
                # Observe a genuine database lock wait, without racing a sleep
                # against statement dispatch. This isolated database has only
                # this submission and the test's owner/observer connections.
                async with database.sessions() as observer:
                    with anyio.fail_after(2):
                        while not await observer.scalar(text(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                            "WHERE datname=current_database() AND pid<>pg_backend_pid() "
                            "AND state='active' AND wait_event_type='Lock')"
                        )):
                            await observer.execute(text('SELECT pg_stat_clear_snapshot()'))
                            await anyio.sleep(.01)
                # The old profile-first path held this row while waiting for
                # a users FK lock, making finalization deadlock on the profile.
                await finalizer.scalar(select(profiles.c.version).where(
                    profiles.c.user_id == owner).with_for_update(nowait=True))
                if erasure_lock:
                    # Erasure must be able to upgrade to the lock required by
                    # its final users DELETE. A waiting writer's KEY SHARE
                    # ownership lock would prevent that upgrade.
                    await finalizer.scalar(select(users.c.id).where(
                        users.c.id == owner).with_for_update(nowait=True))
            with anyio.fail_after(2):
                request = await task
            assert request.state == 'PENDING'
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await database.dispose()
    anyio.run(exercise)

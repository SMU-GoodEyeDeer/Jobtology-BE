from datetime import timedelta
from uuid import uuid4

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, update

from jobtology_be.api.errors import register_error_handlers
from jobtology_be.infrastructure.persistence.compute_budget import (
    AnalysisBudgetExceeded,
    AnalysisBudgetPolicy,
    reserve_budget,
)
from jobtology_be.infrastructure.persistence.contracts import RecomputeRequestCreate, UserCreate
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.queries import PostgresProductQueries
from jobtology_be.infrastructure.persistence.schema import (
    account_rate_limits,
    outbox_jobs,
    recompute_requests,
    user_state_events,
    users,
)
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore

pytest_plugins = ("test_acceptance_persistence",)


def test_budget_error_is_retryable_and_has_no_private_details():
    app = FastAPI()
    register_error_handlers(app)
    @app.get('/limited')
    def limited():
        raise AnalysisBudgetExceeded(17)
    with TestClient(app) as client:
        response = client.get('/limited')
    assert response.status_code == 429
    assert response.headers['Retry-After'] == '17'
    assert response.json()['error']['code'] == 'ANALYSIS_RATE_LIMITED'


def test_atomic_bounded_budget_survives_workers_and_rolls_back(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        owner, other = uuid4(), uuid4()
        try:
            async with database.sessions.begin() as session:
                await session.execute(insert(users), [{'id': owner}, {'id': other}])
                # Pin the race to one window even on the clock boundary.
                await session.execute(insert(account_rate_limits).values(
                    user_id=owner, scope='test_budget', request_count=1,
                    window_started_at=func.date_trunc('hour', func.clock_timestamp()) + timedelta(hours=1),
                ))
            outcomes = []
            async def consume():
                try:
                    async with database.sessions.begin() as session:
                        await reserve_budget(session, owner, scope='test_budget', limit=4, unit='hour')
                    outcomes.append(True)
                except AnalysisBudgetExceeded as error:
                    assert error.retry_after_seconds >= 1
                    outcomes.append(False)
            async with anyio.create_task_group() as tasks:
                for _ in range(16):
                    tasks.start_soon(consume)
            assert sum(outcomes) == 3
            async with database.sessions.begin() as session:
                assert await session.scalar(select(account_rate_limits.c.request_count).where(
                    account_rate_limits.c.user_id == owner)) == 4
                await reserve_budget(session, other, scope='test_budget', limit=4, unit='hour')
            with pytest.raises(RuntimeError):
                async with database.sessions.begin() as session:
                    await reserve_budget(session, other, scope='test_budget', limit=4, unit='hour')
                    raise RuntimeError('transaction rolled back')
            async with database.sessions.begin() as session:
                assert await session.scalar(select(account_rate_limits.c.request_count).where(
                    account_rate_limits.c.user_id == other)) == 1
                await session.execute(update(account_rate_limits).where(
                    account_rate_limits.c.user_id == owner).values(
                    window_started_at=func.clock_timestamp() - timedelta(hours=2)))
                await reserve_budget(session, owner, scope='test_budget', limit=4, unit='hour')
                assert await session.scalar(select(account_rate_limits.c.request_count).where(
                    account_rate_limits.c.user_id == owner)) == 1
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_automatic_work_is_serialized_and_deferred_without_failing_edits(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        policy = AnalysisBudgetPolicy(executions_per_hour=1, executions_per_minute=1)
        store = PostgresApplicationStore(database, analysis_budget_policy=policy)
        other_worker = PostgresApplicationStore(database, analysis_budget_policy=policy)
        owner, other = uuid4(), uuid4()
        async def enqueue(user_id):
            event_id = uuid4()
            async with database.sessions.begin() as session:
                await session.execute(insert(user_state_events).values(
                    id=event_id, user_id=user_id, aggregate_id=event_id, aggregate_type='PROFILE',
                    version=1, kind='PROFILE_UPDATED', payload={},
                ))
            return await store.create_recompute_request(RecomputeRequestCreate(
                user_id=user_id, profile_version=1, trigger_event_id=event_id,
                dedupe_key=str(event_id), payload={}, context={'goal_id': str(uuid4())},
            ))
        try:
            await store.create_user(UserCreate(user_id=owner))
            await store.create_user(UserCreate(user_id=other))
            first, second = await enqueue(owner), await enqueue(owner)
            claimed = []
            async def claim(worker):
                claimed.extend(await worker.claim_recompute_work(10))
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(claim, store)
                tasks.start_soon(claim, other_worker)
            assert len(claimed) == 1
            assert claimed[0].request_id in {first.request_id, second.request_id}
            deferred = ({first.request_id, second.request_id} - {claimed[0].request_id}).pop()
            async with database.sessions.begin() as session:
                await session.execute(update(recompute_requests).where(
                    recompute_requests.c.id == claimed[0].request_id).values(state='READY'))
                await session.execute(update(outbox_jobs).where(
                    outbox_jobs.c.recompute_request_id == claimed[0].request_id).values(
                    state='COMPLETED', lease_token=None, lease_until=None))
                await session.execute(update(outbox_jobs).where(
                    outbox_jobs.c.recompute_request_id == deferred).values(available_at=func.clock_timestamp()))
            another = await enqueue(other)
            assert [work.request_id for work in await other_worker.claim_recompute_work(10)] == [another.request_id]
            queued = await PostgresProductQueries(database).get_recompute(owner, deferred)
            assert queued.state == 'PENDING'
            assert queued.next_attempt_at is not None and queued.next_attempt_at.utcoffset() is not None
            async with database.sessions.begin() as session:
                state = (await session.execute(select(
                    recompute_requests.c.state, outbox_jobs.c.state.label('job_state'),
                    outbox_jobs.c.attempt_count, outbox_jobs.c.available_at > func.clock_timestamp(),
                ).join(outbox_jobs, outbox_jobs.c.recompute_request_id == recompute_requests.c.id)
                  .where(recompute_requests.c.id == deferred))).one()
                assert tuple(state) == ('PENDING', 'READY', 0, True)
                assert await session.scalar(select(account_rate_limits.c.request_count).where(
                    account_rate_limits.c.user_id == owner,
                    account_rate_limits.c.scope == 'analysis_compute_hour')) == 1
                # Advance counters and queue visibility; a restart can claim later.
                await session.execute(update(account_rate_limits).where(
                    account_rate_limits.c.user_id == owner).values(
                    window_started_at=func.clock_timestamp() - timedelta(hours=2)))
                await session.execute(update(outbox_jobs).where(
                    outbox_jobs.c.recompute_request_id == deferred).values(available_at=func.clock_timestamp()))
            assert [work.request_id for work in await other_worker.claim_recompute_work(10)] == [deferred]
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_claim_skips_account_finalizer_before_budget_foreign_key_lock(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        store = PostgresApplicationStore(database)
        owner, event_id = uuid4(), uuid4()
        try:
            await store.create_user(UserCreate(user_id=owner))
            async with database.sessions.begin() as session:
                await session.execute(insert(user_state_events).values(
                    id=event_id, user_id=owner, aggregate_id=event_id, aggregate_type='PROFILE',
                    version=1, kind='PROFILE_UPDATED', payload={},
                ))
            request = await store.create_recompute_request(RecomputeRequestCreate(
                user_id=owner, profile_version=1, trigger_event_id=event_id,
                dedupe_key=str(event_id), payload={}, context={'goal_id': str(uuid4())},
            ))
            # Finalizers and erasure lock the account before the profile. Claim
            # must skip this owner before first-time budget FK insertion could
            # wait behind the account lock while holding the profile itself.
            async with database.sessions.begin() as finalizer:
                await finalizer.scalar(select(users.c.id).where(
                    users.c.id == owner).with_for_update())
                with anyio.fail_after(2):
                    assert await store.claim_recompute_work(10) == ()
                assert await finalizer.scalar(select(func.count()).select_from(
                    account_rate_limits).where(account_rate_limits.c.user_id == owner)) == 0
                assert await finalizer.scalar(select(outbox_jobs.c.attempt_count).where(
                    outbox_jobs.c.recompute_request_id == request.request_id)) == 0
            claimed = await store.claim_recompute_work(10)
            assert [work.request_id for work in claimed] == [request.request_id]
        finally:
            await database.dispose()
    anyio.run(exercise)

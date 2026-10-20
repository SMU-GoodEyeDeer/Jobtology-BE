from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import anyio
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from test_acceptance_m5_configured_mutations import _pending_context, _process_one
from test_acceptance_m5_lifecycle import _application
from test_acceptance_m5_worker import process_recompute

from jobtology_be.infrastructure.persistence.auth_store import prune_auth_records
from jobtology_be.infrastructure.persistence.contracts import (
    IdempotencyAcquire,
    StepStateMutation,
    UserCreate,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    auth_sessions,
    idempotency_records,
    oauth_login_attempts,
)
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore

pytest_plugins = ("test_acceptance_m5_lifecycle",)


async def _exercise_step_progress(database_url, user_id, roadmap_id, step_id):
    database = Database.create(database_url)
    store = PostgresApplicationStore(database, capability_list_authoritative=True)
    try:
        for offset, state in enumerate(("IN_PROGRESS", "TODO", "COMPLETED", "TODO")):
            await store.mutate_step_state(StepStateMutation(
                user_id=user_id, roadmap_id=roadmap_id, step_id=step_id,
                expected_profile_version=2 + offset, expected_roadmap_version=2 + offset,
                state=state,
            ))
            context = await _pending_context(database, user_id, 3 + offset)
            assert context.completeness.entities_complete
            assert bool(context.capabilities) == (state == "COMPLETED")
            await _process_one(store)
    finally:
        await database.dispose()


def test_all_step_transitions_keep_executable_context_and_authoritative_empty_specs(
    acceptance_database_url,
):
    user_id = uuid4()
    target = datetime.now(UTC) + timedelta(days=30)
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        goal = client.post("/api/v1/me/goals", json={
            "expected_profile_version": 1, "goal_mode": "TARGETED",
            "occupation_id": "BACKEND_DEVELOPER", "target_by": target.isoformat(),
            "timezone": "UTC", "original_time_phrase": "within a month",
        })
    assert goal.status_code == 201
    goal_id = UUID(goal.json()["goal_id"])
    publication = anyio.run(process_recompute, acceptance_database_url, user_id, 2, goal_id, (), target)
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        roadmap = client.post("/api/v1/roadmaps", json={
            "expected_profile_version": 2, "goal_id": str(goal_id),
            "proposal_id": str(publication.proposal_id), "title": "Progress transitions",
        })
        assert roadmap.status_code == 201
        roadmap_id = UUID(roadmap.json()["roadmap_id"])
        activated = client.patch(f"/api/v1/roadmaps/{roadmap_id}", json={
            "operation": "ACTIVATE", "expected_profile_version": 2, "expected_roadmap_version": 1,
        })
        assert activated.status_code == 200
        step_id = UUID(client.get(f"/api/v1/roadmaps/{roadmap_id}").json()["steps"][0]["step_id"])
    anyio.run(_exercise_step_progress, acceptance_database_url, user_id, roadmap_id, step_id)


async def _exercise_retention(database_url):
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)
    old_user, new_user = uuid4(), uuid4()
    expired = datetime.now(UTC) - timedelta(days=1)
    future = datetime.now(UTC) + timedelta(days=1)
    try:
        for user_id in (old_user, new_user):
            await store.create_user(UserCreate(user_id=user_id))
        async with database.sessions.begin() as session:
            for number, (expires, revoked) in enumerate(
                ((future, None), (expired, None), (future, expired)), start=1,
            ):
                await session.execute(insert(auth_sessions).values(
                    id=uuid4(), user_id=old_user, token_hash=bytes([number]) * 32,
                    csrf_hash=b'c' * 32, expires_at=expires, revoked_at=revoked,
                ))
            for number, expires in enumerate((future, expired), start=1):
                await session.execute(insert(oauth_login_attempts).values(
                    state_hash=bytes([number]) * 32, browser_binding_hash=b'b' * 32,
                    nonce="nonce", pkce_verifier="verifier", expires_at=expires,
                ))
            for key, expires, status in (
                ("expired-complete", expired, 200),
                ("active-complete", future, 200),
                ("expired-ambiguous", expired, None),
            ):
                await session.execute(insert(idempotency_records).values(
                    user_id=old_user, method="POST", path="/old-path", key=key,
                    request_hash="request", response_status=status,
                    response={"ok": True} if status else None, expires_at=expires,
                ))
            await prune_auth_records(session)
        # New account/path/key traffic also prunes old, unrelated replay records.
        await store.acquire_idempotency(IdempotencyAcquire(
            user_id=new_user, method="POST", path="/new-path", key="new-key",
            request_hash="new-request", expires_at=future,
        ))
        async with database.sessions() as session:
            assert (await session.scalars(select(auth_sessions.c.token_hash))).all() == [b'\x01' * 32]
            assert (await session.scalars(select(oauth_login_attempts.c.state_hash))).all() == [b'\x01' * 32]
            assert set((await session.scalars(select(idempotency_records.c.key))).all()) == {
                "active-complete", "expired-ambiguous", "new-key",
            }
    finally:
        await database.dispose()


def test_retention_removes_unusable_credentials_and_unrelated_expired_completed_replays(
    acceptance_database_url,
):
    anyio.run(_exercise_retention, acceptance_database_url)

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.infrastructure.persistence.contracts import (
    GoalMutation,
    IdempotencyAcquire,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    JsonValue,
    UserCreate,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import goals
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

pytest_plugins = ("test_acceptance_persistence",)


class OperationFailure(Exception):
    pass


class StaticIdentityProvider:
    user_id: UUID

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


async def _create_user(database_url: str, user_id: UUID) -> None:
    database = Database.create(database_url)
    try:
        await PostgresApplicationStore(database).create_user(UserCreate(user_id=user_id))
    finally:
        await database.dispose()


async def _goal_count(database_url: str, user_id: UUID) -> int:
    database = Database.create(database_url)
    try:
        async with database.sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(goals).where(goals.c.user_id == user_id)
            )
        assert count is not None
        return count
    finally:
        await database.dispose()


async def _exercise_atomic_idempotency(database_url: str) -> None:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)
    user_id = uuid4()
    request = IdempotencyAcquire(
        user_id=user_id,
        method="POST",
        path="/api/v1/me/goals",
        key="atomic-goal-create",
        request_hash="request-hash",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    try:
        await store.create_user(UserCreate(user_id=user_id))

        async def failed_operation() -> Mapping[str, JsonValue]:
            _ = await store.mutate_goal(
                GoalMutation(
                    user_id=user_id,
                    expected_profile_version=1,
                    goal_id=None,
                    goal_mode="TARGETED",
                    occupation_id="BACKEND_DEVELOPER",
                    target_by=datetime(2027, 1, 1, tzinfo=UTC),
                    timezone="UTC",
                    original_time_phrase="by next year",
                    status="ACTIVE",
                )
            )
            raise OperationFailure

        with pytest.raises(OperationFailure):
            await store.execute_idempotency(request, 201, failed_operation)

        async def successful_operation() -> Mapping[str, JsonValue]:
            goal = await store.mutate_goal(
                GoalMutation(
                    user_id=user_id,
                    expected_profile_version=1,
                    goal_id=None,
                    goal_mode="TARGETED",
                    occupation_id="BACKEND_DEVELOPER",
                    target_by=datetime(2027, 1, 1, tzinfo=UTC),
                    timezone="UTC",
                    original_time_phrase="by next year",
                    status="ACTIVE",
                )
            )
            return {"goal_id": str(goal.goal_id), "status": "ACTIVE"}

        completed = await store.execute_idempotency(request, 201, successful_operation)
        assert completed.response_status == 201
        assert completed.response is not None

        async def replay_operation() -> Mapping[str, JsonValue]:
            raise AssertionError("a committed idempotency request must replay without mutation")

        restarted_database = Database.create(database_url)
        restarted_store = PostgresApplicationStore(restarted_database)
        try:
            replayed = await restarted_store.execute_idempotency(request, 201, replay_operation)
            assert replayed == completed

            async with restarted_database.sessions() as session:
                goal_count = await session.scalar(
                    select(func.count()).select_from(goals).where(goals.c.user_id == user_id)
                )
            assert goal_count == 1

            with pytest.raises(IdempotencyConflictError):
                await restarted_store.execute_idempotency(
                    IdempotencyAcquire(
                        user_id=request.user_id,
                        method=request.method,
                        path=request.path,
                        key=request.key,
                        request_hash="changed-request-hash",
                        expires_at=request.expires_at,
                    ),
                    201,
                    replay_operation,
                )
        finally:
            await restarted_database.dispose()

        pending_request = IdempotencyAcquire(
            user_id=user_id,
            method="POST",
            path="/api/v1/me/goals",
            key="ambiguous-pending",
            request_hash="pending-request-hash",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        await store.acquire_idempotency(pending_request)
        with pytest.raises(IdempotencyInProgressError):
            await store.acquire_idempotency(
                IdempotencyAcquire(
                    user_id=pending_request.user_id,
                    method=pending_request.method,
                    path=pending_request.path,
                    key=pending_request.key,
                    request_hash=pending_request.request_hash,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
    finally:
        await database.dispose()


def test_postgres_idempotency_rolls_back_failed_mutations_and_replays_committed_results(
    acceptance_database_url: str,
) -> None:
    # Given
    database_url = acceptance_database_url

    # When / Then
    anyio.run(_exercise_atomic_idempotency, database_url)


def test_postgres_goal_endpoint_replays_same_key_and_rejects_a_changed_request(
    acceptance_database_url: str,
) -> None:
    database_url = acceptance_database_url
    user_id = uuid4()
    anyio.run(_create_user, database_url, user_id)
    app = create_app(
        Settings(environment="test", database_url=database_url, enable_fixtures=False),
        dependencies=ApiDependencies(identity_provider=StaticIdentityProvider(user_id)),
    )
    payload = {
        "expected_profile_version": 1,
        "goal_mode": "TARGETED",
        "occupation_id": "BACKEND_DEVELOPER",
        "target_by": "2027-01-01T00:00:00+00:00",
        "timezone": "UTC",
        "original_time_phrase": "by next year",
    }

    with TestClient(app) as client:
        first_response = client.post(
            "/api/v1/me/goals",
            headers={"Idempotency-Key": "http-goal-create"},
            json=payload,
        )
        replay_response = client.post(
            "/api/v1/me/goals",
            headers={"Idempotency-Key": "http-goal-create"},
            json=payload,
        )
        conflicting_response = client.post(
            "/api/v1/me/goals",
            headers={"Idempotency-Key": "http-goal-create"},
            json={**payload, "original_time_phrase": "by the following year"},
        )

    assert first_response.status_code == 201
    assert replay_response.status_code == 201
    assert replay_response.json() == first_response.json()
    assert conflicting_response.status_code == 409
    assert conflicting_response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert anyio.run(_goal_count, database_url, user_id) == 1

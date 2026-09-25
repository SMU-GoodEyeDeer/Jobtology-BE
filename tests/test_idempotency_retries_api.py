from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID

from fastapi import HTTPException
from fastapi.testclient import TestClient

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.services.goals import GoalService, GoalUpdateCommand, GoalUpdateResult
from jobtology_be.infrastructure.persistence.contracts import (
    IdempotencyAcquire,
    IdempotencyComplete,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    IdempotencySnapshot,
    JsonValue,
)
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

USER_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
GOAL_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b5")


class StaticIdentityProvider:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=USER_ID)


class FailingOnceGoalService(GoalService):
    call_count: int = 0

    async def update_goal(self, user_id: UUID, command: GoalUpdateCommand) -> GoalUpdateResult:
        self.call_count += 1
        if self.call_count == 1:
            raise HTTPException(status_code=503)
        return GoalUpdateResult(goal_id=GOAL_ID, status="ACTIVE")


class AtomicMemoryIdempotencyStore:
    _hashes: dict[tuple[UUID, str, str, str], str]
    _responses: dict[tuple[UUID, str, str, str], IdempotencySnapshot]

    def __init__(self) -> None:
        self._hashes = {}
        self._responses = {}

    async def acquire_idempotency(self, request: IdempotencyAcquire) -> IdempotencySnapshot:
        key = _key(request)
        existing = self._responses.get(key)
        if existing is None:
            self._hashes[key] = request.request_hash
            snapshot = IdempotencySnapshot(response_status=None, response=None)
            self._responses[key] = snapshot
            return snapshot
        if self._hashes[key] != request.request_hash:
            raise IdempotencyConflictError(key=request.key)
        if existing.response_status is None:
            raise IdempotencyInProgressError(key=request.key)
        return existing

    async def complete_idempotency(self, request: IdempotencyComplete) -> None:
        key = _key(request.request)
        self._responses[key] = IdempotencySnapshot(
            response_status=request.response_status,
            response=request.response,
        )

    async def execute_idempotency(
        self,
        request: IdempotencyAcquire,
        response_status: int,
        operation: Callable[[], Awaitable[Mapping[str, JsonValue]]],
    ) -> IdempotencySnapshot:
        key = _key(request)
        existing = self._responses.get(key)
        if existing is not None:
            if self._hashes[key] != request.request_hash:
                raise IdempotencyConflictError(key=request.key)
            if existing.response_status is None:
                raise IdempotencyInProgressError(key=request.key)
            return existing
        response = await operation()
        snapshot = IdempotencySnapshot(response_status=response_status, response=response)
        self._hashes[key] = request.request_hash
        self._responses[key] = snapshot
        return snapshot


def _key(request: IdempotencyAcquire) -> tuple[UUID, str, str, str]:
    return (request.user_id, request.method, request.path, request.key)


def test_goal_creation_retries_the_same_key_when_the_pre_mutation_operation_fails() -> None:
    # Given
    service = FailingOnceGoalService()
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            goal_service=service,
            idempotency_store=AtomicMemoryIdempotencyStore(),
        ),
    )
    payload = {
        "expected_profile_version": 3,
        "goal_mode": "TARGETED",
        "occupation_id": "occupation:backend-engineer",
        "target_by": "2027-01-01T09:00:00+00:00",
        "timezone": "UTC",
        "original_time_phrase": "by next year",
    }

    # When
    with TestClient(app, raise_server_exceptions=False) as client:
        failed_response = client.post(
            "/api/v1/me/goals",
            headers={"Idempotency-Key": "retry-after-failure"},
            json=payload,
        )
        retry_response = client.post(
            "/api/v1/me/goals",
            headers={"Idempotency-Key": "retry-after-failure"},
            json=payload,
        )

    # Then
    assert failed_response.status_code == 503
    assert retry_response.status_code == 201
    assert retry_response.json() == {"goal_id": str(GOAL_ID), "status": "ACTIVE"}
    assert service.call_count == 2

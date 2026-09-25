from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID

from fastapi.testclient import TestClient

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.services.capabilities import (
    CapabilityMutationCommand,
    CapabilityResult,
    CapabilityService,
)
from jobtology_be.infrastructure.persistence.contracts import (
    IdempotencyAcquire,
    IdempotencyComplete,
    IdempotencySnapshot,
    JsonValue,
)
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

USER_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
CAPABILITY_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b5")


class StaticIdentityProvider:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=USER_ID)


class RecordingCapabilityService(CapabilityService):
    command: CapabilityMutationCommand | None = None
    upsert_count: int = 0

    async def upsert(self, user_id: UUID, command: CapabilityMutationCommand) -> CapabilityResult:
        self.command = command
        self.upsert_count += 1
        return CapabilityResult(capability_id=CAPABILITY_ID, profile_version=4)

    async def delete(self, user_id: UUID, capability_id: UUID, expected_profile_version: int) -> int:
        return expected_profile_version + 1


class MemoryIdempotencyStore:
    _responses: dict[tuple[UUID, str, str, str], IdempotencySnapshot]

    def __init__(self) -> None:
        self._responses = {}

    async def acquire_idempotency(self, request: IdempotencyAcquire) -> IdempotencySnapshot:
        key = (request.user_id, request.method, request.path, request.key)
        snapshot = self._responses.get(key)
        if snapshot is None:
            snapshot = IdempotencySnapshot(response_status=None, response=None)
            self._responses[key] = snapshot
        return snapshot

    async def complete_idempotency(self, request: IdempotencyComplete) -> None:
        key = (
            request.request.user_id,
            request.request.method,
            request.request.path,
            request.request.key,
        )
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
        key = (request.user_id, request.method, request.path, request.key)
        existing = self._responses.get(key)
        if existing is not None:
            return existing
        response = await operation()
        snapshot = IdempotencySnapshot(response_status=response_status, response=response)
        self._responses[key] = snapshot
        return snapshot


def test_capability_create_uses_an_authenticated_principal_and_returns_persisted_id() -> None:
    # Given
    service = RecordingCapabilityService()
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            capability_service=service,
        ),
    )

    # When
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/me/capabilities",
            json={
                "expected_profile_version": 3,
                "category": "SKILL",
                "raw_text": "FastAPI",
                "entity_id": "capability:fastapi",
                "proficiency": "INTERMEDIATE",
                "details": {"source": "self"},
            },
        )

    # Then
    assert response.status_code == 201
    assert response.json() == {
        "capability_id": str(CAPABILITY_ID),
        "profile_version": 4,
    }
    assert service.command == CapabilityMutationCommand(
        capability_id=None,
        expected_profile_version=3,
        category="SKILL",
        raw_text="FastAPI",
        entity_id="capability:fastapi",
        proficiency="INTERMEDIATE",
        details={"source": "self"},
    )


def test_capability_create_replays_completed_idempotent_request_without_a_second_mutation() -> None:
    # Given
    service = RecordingCapabilityService()
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            capability_service=service,
            idempotency_store=MemoryIdempotencyStore(),
        ),
    )
    payload = {
        "expected_profile_version": 3,
        "category": "SKILL",
        "raw_text": "FastAPI",
        "entity_id": "capability:fastapi",
        "proficiency": "INTERMEDIATE",
        "details": {"source": "self"},
    }

    # When
    with TestClient(app) as client:
        first_response = client.post(
            "/api/v1/me/capabilities",
            headers={"Idempotency-Key": "capability-create-1"},
            json=payload,
        )
        replay_response = client.post(
            "/api/v1/me/capabilities",
            headers={"Idempotency-Key": "capability-create-1"},
            json=payload,
        )

    # Then
    assert first_response.status_code == 201
    assert replay_response.status_code == 201
    assert replay_response.json() == first_response.json()
    assert service.upsert_count == 1

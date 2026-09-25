from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID

from fastapi.testclient import TestClient

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.services.goals import GoalService, GoalUpdateCommand, GoalUpdateResult
from jobtology_be.infrastructure.persistence.contracts import (
    IdempotencyAcquire,
    IdempotencyComplete,
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


class RecordingGoalService(GoalService):
    command: GoalUpdateCommand | None = None

    async def update_goal(self, user_id: UUID, command: GoalUpdateCommand) -> GoalUpdateResult:
        self.command = command
        return GoalUpdateResult(goal_id=GOAL_ID, status="ACTIVE")


class ReplayIdempotencyStore:
    async def acquire_idempotency(self, request: IdempotencyAcquire) -> IdempotencySnapshot:
        return IdempotencySnapshot(
            response_status=201,
            response={"goal_id": str(GOAL_ID), "status": "ACTIVE"},
        )

    async def complete_idempotency(self, request: IdempotencyComplete) -> None:
        raise AssertionError("replayed requests must not be completed again")

    async def execute_idempotency(
        self,
        request: IdempotencyAcquire,
        response_status: int,
        operation: Callable[[], Awaitable[Mapping[str, JsonValue]]],
    ) -> IdempotencySnapshot:
        return IdempotencySnapshot(
            response_status=201,
            response={"goal_id": str(GOAL_ID), "status": "ACTIVE"},
        )


def test_goal_create_replays_an_idempotent_response_without_running_the_service() -> None:
    # Given
    service = RecordingGoalService()
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            goal_service=service,
            idempotency_store=ReplayIdempotencyStore(),
        ),
    )

    # When
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/me/goals",
            headers={"Idempotency-Key": "goal-create-1"},
            json={
                "expected_profile_version": 3,
                "goal_mode": "TARGETED",
                "occupation_id": "occupation:backend-engineer",
                "target_by": "2027-01-01T09:00:00+00:00",
                "timezone": "UTC",
                "original_time_phrase": "by next year",
            },
        )

    # Then
    assert response.status_code == 201
    assert response.json() == {"goal_id": str(GOAL_ID), "status": "ACTIVE"}
    assert service.command is None

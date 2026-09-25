from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID

from fastapi.testclient import TestClient

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.queries import RoadmapView
from jobtology_be.application.services.roadmaps import (
    RoadmapCreateCommand,
    RoadmapMutationCommand,
    RoadmapResult,
    RoadmapService,
    StepStateCommand,
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
ROADMAP_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b5")
GOAL_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b6")
PROPOSAL_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b7")


class StaticIdentityProvider:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=USER_ID)


class RecordingRoadmapService(RoadmapService):
    create_command: RoadmapCreateCommand | None = None
    mutation_command: RoadmapMutationCommand | None = None
    step_command: StepStateCommand | None = None

    async def create(self, user_id: UUID, command: RoadmapCreateCommand) -> RoadmapResult:
        self.create_command = command
        return RoadmapResult(roadmap_id=ROADMAP_ID, roadmap_version=1, state="DRAFT")

    async def mutate(self, user_id: UUID, command: RoadmapMutationCommand) -> RoadmapResult:
        self.mutation_command = command
        return RoadmapResult(
            roadmap_id=command.roadmap_id,
            roadmap_version=command.expected_roadmap_version + 1,
            state=command.state,
        )

    async def update_step(self, user_id: UUID, command: StepStateCommand) -> RoadmapResult:
        self.step_command = command
        return RoadmapResult(
            roadmap_id=command.roadmap_id,
            roadmap_version=command.expected_roadmap_version + 1,
            state="ACTIVE",
        )


class StaticProductQueries:
    async def list_roadmaps(self, user_id: UUID) -> tuple[RoadmapView, ...]:
        return (
            RoadmapView(
                roadmap_id=ROADMAP_ID,
                roadmap_version=2,
                state="ACTIVE",
                goal_id=GOAL_ID,
                proposal_id=PROPOSAL_ID,
                title="Backend roadmap",
                profile_version=3,
                release_id="release-reviewed-v1",
                validity={},
                steps=(),
            ),
        )

    async def get_roadmap(self, user_id: UUID, roadmap_id: UUID) -> RoadmapView:
        return (await self.list_roadmaps(user_id))[0]


class ReplayIdempotencyStore:
    async def acquire_idempotency(self, request: IdempotencyAcquire) -> IdempotencySnapshot:
        return IdempotencySnapshot(
            response_status=201,
            response={
                "roadmap_id": str(ROADMAP_ID),
                "roadmap_version": 1,
                "state": "DRAFT",
            },
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
            response={
                "roadmap_id": str(ROADMAP_ID),
                "roadmap_version": 1,
                "state": "DRAFT",
            },
        )

def test_roadmap_routes_map_authenticated_lifecycle_commands() -> None:
    # Given
    service = RecordingRoadmapService()
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            roadmap_service=service,
            product_queries=StaticProductQueries(),
        ),
    )

    # When
    with TestClient(app) as client:
        create_response = client.post(
            "/api/v1/roadmaps",
            json={
                "expected_profile_version": 3,
                "goal_id": str(GOAL_ID),
                "proposal_id": str(PROPOSAL_ID),
                "title": "Backend roadmap",
            },
        )
        activate_response = client.patch(
            f"/api/v1/roadmaps/{ROADMAP_ID}",
            json={
                "operation": "ACTIVATE",
                "expected_profile_version": 3,
                "expected_roadmap_version": 1,
            },
        )
        rename_response = client.patch(
            f"/api/v1/roadmaps/{ROADMAP_ID}",
            json={
                "operation": "RENAME",
                "expected_profile_version": 3,
                "expected_roadmap_version": 2,
                "title": "Renamed roadmap",
            },
        )

    # Then
    assert create_response.status_code == 201
    assert activate_response.status_code == 200
    assert rename_response.status_code == 200
    assert service.create_command is not None
    assert service.create_command.goal_id == GOAL_ID
    assert service.mutation_command is not None
    assert service.mutation_command.state == "ACTIVE"
    assert service.mutation_command.title == "Renamed roadmap"


def test_roadmap_create_rejects_client_controlled_source_metadata() -> None:
    # Given
    service = RecordingRoadmapService()
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            roadmap_service=service,
        ),
    )

    # When
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/roadmaps",
            json={
                "expected_profile_version": 3,
                "goal_id": str(GOAL_ID),
                "proposal_id": str(PROPOSAL_ID),
                "title": "Backend roadmap",
                "release_id": "attacker-controlled-release",
                "validity": {"source_is_valid": False},
            },
        )

    # Then
    assert response.status_code == 422
    assert service.create_command is None


def test_roadmap_list_and_detail_use_the_authenticated_query_scope() -> None:
    # Given
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            product_queries=StaticProductQueries(),
        ),
    )

    # When
    with TestClient(app) as client:
        list_response = client.get("/api/v1/roadmaps")
        detail_response = client.get(f"/api/v1/roadmaps/{ROADMAP_ID}")

    # Then
    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert list_response.json()["items"][0]["roadmap_id"] == str(ROADMAP_ID)
    assert detail_response.json()["roadmap_version"] == 2


def test_roadmap_create_replays_an_idempotent_response_without_running_the_service() -> None:
    # Given
    service = RecordingRoadmapService()
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            roadmap_service=service,
            idempotency_store=ReplayIdempotencyStore(),
        ),
    )

    # When
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/roadmaps",
            headers={"Idempotency-Key": "roadmap-create-1"},
            json={
                "expected_profile_version": 3,
                "goal_id": str(GOAL_ID),
                "proposal_id": str(PROPOSAL_ID),
                "title": "Backend roadmap",
            },
        )

    # Then
    assert response.status_code == 201
    assert response.json() == {
        "roadmap_id": str(ROADMAP_ID),
        "roadmap_version": 1,
        "state": "DRAFT",
    }
    assert service.create_command is None

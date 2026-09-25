from datetime import UTC, date, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.services.goals import GoalUpdateCommand, GoalUpdateResult
from jobtology_be.application.services.profiles import ProfileUpdateCommand, ProfileUpdateResult
from jobtology_be.main import create_app
from jobtology_be.settings import Settings


class StaticIdentityProvider:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4"))


class RecordingProfileService:
    user_id: UUID | None
    command: ProfileUpdateCommand | None

    def __init__(self) -> None:
        self.user_id = None
        self.command = None

    async def update_profile(
        self, user_id: UUID, command: ProfileUpdateCommand
    ) -> ProfileUpdateResult:
        self.user_id = user_id
        self.command = command
        return ProfileUpdateResult(user_id=user_id, profile_version=2)


class RecordingGoalService:
    user_id: UUID | None
    command: GoalUpdateCommand | None

    def __init__(self) -> None:
        self.user_id = None
        self.command = None

    async def update_goal(self, user_id: UUID, command: GoalUpdateCommand) -> GoalUpdateResult:
        self.user_id = user_id
        self.command = command
        return GoalUpdateResult(
            goal_id=UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4"), status="ACTIVE"
        )


def test_product_profile_rejects_request_supplied_identity_by_default() -> None:
    # Given
    app = create_app(Settings(enable_fixtures=False))

    with TestClient(app) as client:
        # When
        response = client.put(
            "/api/v1/me/profile",
            headers={"X-User-ID": "attacker"},
            json={
                "expected_profile_version": 1,
                "major_raw": "Computer Science",
            },
        )

    # Then
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_product_routes_accept_only_injected_identity_provider() -> None:
    # Given
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(identity_provider=StaticIdentityProvider()),
    )

    with TestClient(app) as client:
        # When
        response = client.put(
            "/api/v1/me/profile",
            headers={"X-User-ID": "attacker"},
            json={
                "expected_profile_version": 1,
                "major_raw": "Computer Science",
            },
        )

    # Then
    assert response.status_code == 503


def test_profile_update_routes_validated_command_to_injected_service() -> None:
    # Given
    profile_service = RecordingProfileService()
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            profile_service=profile_service,
        ),
    )

    with TestClient(app) as client:
        # When
        response = client.put(
            "/api/v1/me/profile",
            json={
                "expected_profile_version": 1,
                "major_raw": "Computer Science",
                "major_concept_id": "concept:computer-science",
                "year": 3,
                "enrollment_status": "ENROLLED",
                "expected_graduation_on": "2028-02-01",
            },
        )

    # Then
    assert response.status_code == 200
    assert response.json() == {
        "user_id": "d0b3d8d1-7de4-4a62-8c58-e652431377b4",
        "profile_version": 2,
    }
    assert profile_service.user_id == UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
    assert profile_service.command == ProfileUpdateCommand(
        expected_profile_version=1,
        major_raw="Computer Science",
        major_concept_id="concept:computer-science",
        year=3,
        enrollment_status="ENROLLED",
        expected_graduation_on=date(2028, 2, 1),
    )


def test_goal_creation_fails_closed_without_an_injected_identity() -> None:
    # Given
    app = create_app(Settings(_env_file=None, enable_fixtures=False))

    # When
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/me/goals",
            json={
                "expected_profile_version": 1,
                "goal_mode": "TARGETED",
                "occupation_id": "BACKEND_DEVELOPER",
                "target_by": "2027-01-01T00:00:00+00:00",
                "timezone": "Asia/Seoul",
                "original_time_phrase": "next year",
            },
        )

    # Then
    assert response.status_code == 401


def test_goal_creation_routes_the_trusted_principal_to_the_injected_service() -> None:
    # Given
    goal_service = RecordingGoalService()
    app = create_app(
        Settings(_env_file=None, enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            goal_service=goal_service,
        ),
    )

    # When
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/me/goals",
            json={
                "expected_profile_version": 1,
                "goal_mode": "TARGETED",
                "occupation_id": "BACKEND_DEVELOPER",
                "target_by": "2027-01-01T00:00:00+00:00",
                "timezone": "Asia/Seoul",
                "original_time_phrase": "next year",
            },
        )

    # Then
    assert response.status_code == 201
    assert response.json() == {
        "goal_id": "d0b3d8d1-7de4-4a62-8c58-e652431377b4",
        "status": "ACTIVE",
    }
    assert goal_service.user_id == UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
    assert goal_service.command == GoalUpdateCommand(
        expected_profile_version=1,
        goal_mode="TARGETED",
        occupation_id="BACKEND_DEVELOPER",
        target_by=datetime(2027, 1, 1, tzinfo=UTC),
        timezone="Asia/Seoul",
        original_time_phrase="next year",
        status="ACTIVE",
    )

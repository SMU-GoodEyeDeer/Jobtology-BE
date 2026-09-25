from datetime import date
from uuid import UUID

from fastapi.testclient import TestClient

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.queries import ProfileView
from jobtology_be.main import create_app
from jobtology_be.settings import Settings


class StaticIdentityProvider:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4"))


class StaticProductQueries:
    async def get_profile(self, user_id: UUID) -> ProfileView:
        return ProfileView(
            user_id=user_id,
            profile_version=3,
            major_raw="Computer Science",
            major_concept_id="concept:computer-science",
            year=3,
            enrollment_status="ENROLLED",
            expected_graduation_on=date(2028, 2, 1),
        )


def test_profile_read_uses_the_authenticated_principal_and_injected_query_service() -> None:
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
        response = client.get("/api/v1/me/profile")

    # Then
    assert response.status_code == 200
    assert response.json() == {
        "user_id": "d0b3d8d1-7de4-4a62-8c58-e652431377b4",
        "profile_version": 3,
        "major_raw": "Computer Science",
        "major_concept_id": "concept:computer-science",
        "year": 3,
        "enrollment_status": "ENROLLED",
        "expected_graduation_on": "2028-02-01",
    }

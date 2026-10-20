from dataclasses import dataclass
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.errors import register_error_handlers
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.preferences import require_preferences_service, router
from jobtology_be.application.services.preferences import (
    PersistentPreferencesService,
    PreferencesService,
)
from jobtology_be.infrastructure.persistence.contracts import UserCreate
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.preference_queries import PostgresRoutePreferencesQuery
from jobtology_be.infrastructure.persistence.schema import profiles, route_preferences
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

pytest_plugins = ("test_acceptance_m5_lifecycle",)

PREFERENCES_REQUEST = {
    "expected_profile_version": 1,
    "available_hours_per_week": 4,
    "availability_source": "FLEXIBLE_WEEKLY",
    "budget_mode": "REGULAR",
    "max_out_of_pocket_krw": 300_000,
    "fastest_path": True,
    "needs_portfolio": False,
    "career_switch": True,
}


@dataclass(slots=True)
class SwitchableIdentityProvider:
    user_id: UUID

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


def _app(
    preferences_service: PreferencesService | None,
    identity_provider: SwitchableIdentityProvider,
) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[require_authenticated_principal] = identity_provider.current_principal
    if preferences_service is not None:

        def injected_preferences_service() -> PreferencesService:
            return preferences_service

        app.dependency_overrides[require_preferences_service] = injected_preferences_service
    return app


def test_route_preferences_require_configured_service() -> None:
    # Given
    application = _app(None, SwitchableIdentityProvider(user_id=uuid4()))

    # When
    with TestClient(application) as client:
        response = client.get("/me/route-preferences")

    # Then
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"


async def _exercise_route_preferences_api(database_url: str) -> None:
    database = Database.create(database_url)
    service = PersistentPreferencesService(
        store=PostgresApplicationStore(database),
        query=PostgresRoutePreferencesQuery(database),
    )
    owner_id = uuid4()
    other_user_id = uuid4()
    identity_provider = SwitchableIdentityProvider(user_id=owner_id)
    try:
        with TestClient(_app(service, identity_provider)) as owner_client:
            # Given
            missing_response = owner_client.get("/me/route-preferences")

            # When
            invalid_hours_response = owner_client.put(
                "/me/route-preferences",
                json={**PREFERENCES_REQUEST, "available_hours_per_week": 0},
            )
            invalid_cap_response = owner_client.put(
                "/me/route-preferences",
                json={**PREFERENCES_REQUEST, "max_out_of_pocket_krw": -1},
            )
            invalid_type_response = owner_client.put(
                "/me/route-preferences",
                json={**PREFERENCES_REQUEST, "available_hours_per_week": "4"},
            )
            missing_source_response = owner_client.put(
                "/me/route-preferences",
                json={
                    "expected_profile_version": 1,
                    "available_hours_per_week": 4,
                    "budget_mode": "REGULAR",
                    "max_out_of_pocket_krw": 300_000,
                    "fastest_path": True,
                    "needs_portfolio": False,
                    "career_switch": True,
                },
            )
            update_response = owner_client.put("/me/route-preferences", json=PREFERENCES_REQUEST)
            get_response = owner_client.get("/me/route-preferences")
            stale_response = owner_client.put("/me/route-preferences", json=PREFERENCES_REQUEST)
            identity_provider.user_id = other_user_id
            other_response = owner_client.get("/me/route-preferences")

        # Then
        expected_response = {
            "profile_version": 2,
            "available_hours_per_week": 4,
            "availability_source": "FLEXIBLE_WEEKLY",
            "budget_mode": "REGULAR",
            "max_out_of_pocket_krw": 300_000,
            "fastest_path": True,
            "needs_portfolio": False,
            "career_switch": True,
        }
        assert missing_response.status_code == 404
        assert missing_response.json()["error"]["code"] == "NOT_FOUND"
        assert invalid_hours_response.status_code == 422
        assert invalid_cap_response.status_code == 422
        assert invalid_type_response.status_code == 422
        assert missing_source_response.status_code == 422
        assert update_response.status_code == 200
        assert update_response.json() == expected_response
        assert get_response.status_code == 200
        assert get_response.json() == expected_response
        assert stale_response.status_code == 409
        assert stale_response.json()["error"]["code"] == "VERSION_CONFLICT"
        assert other_response.status_code == 404
    finally:
        await database.dispose()


def test_route_preferences_api_validates_persists_and_scopes_to_the_principal(
    acceptance_database_url: str,
) -> None:
    pytest.importorskip("asyncpg")
    anyio.run(_exercise_route_preferences_api, acceptance_database_url)


def test_configured_app_exposes_route_preferences_api(
    acceptance_database_url: str,
) -> None:
    # Given
    identity_provider = SwitchableIdentityProvider(user_id=uuid4())
    application = create_app(
        Settings(database_url=acceptance_database_url, enable_fixtures=False),
        dependencies=ApiDependencies(identity_provider=identity_provider),
    )

    # When
    with TestClient(application) as client:
        missing_response = client.get("/api/v1/me/route-preferences")
        update_response = client.put("/api/v1/me/route-preferences", json=PREFERENCES_REQUEST)
        get_response = client.get("/api/v1/me/route-preferences")

    # Then
    assert missing_response.status_code == 404
    assert update_response.status_code == 200
    assert update_response.json()["profile_version"] == 2
    assert get_response.json() == update_response.json()


def test_existing_account_without_preferences_reads_analysis_defaults_without_writes(
    acceptance_database_url: str,
) -> None:
    user_id = uuid4()

    async def setup() -> None:
        database = Database.create(acceptance_database_url)
        try:
            await PostgresApplicationStore(database).create_user(UserCreate(user_id=user_id))
        finally:
            await database.dispose()

    async def persisted() -> tuple[int, int]:
        database = Database.create(acceptance_database_url)
        try:
            async with database.sessions() as session:
                version = await session.scalar(select(profiles.c.version).where(
                    profiles.c.user_id == user_id,
                ))
                count = await session.scalar(select(func.count()).select_from(route_preferences))
                return version, count
        finally:
            await database.dispose()

    anyio.run(setup)
    database = Database.create(acceptance_database_url)
    service = PersistentPreferencesService(
        PostgresApplicationStore(database), PostgresRoutePreferencesQuery(database),
    )
    try:
        identity = SwitchableIdentityProvider(user_id)
        with TestClient(_app(service, identity)) as client:
            for _ in range(2):
                response = client.get("/me/route-preferences")
                assert response.status_code == 200
                assert response.json() == {
                    "profile_version": 1, "available_hours_per_week": 10,
                    "availability_source": "DEFAULT", "budget_mode": "REGULAR",
                    "max_out_of_pocket_krw": None, "fastest_path": False,
                    "needs_portfolio": False, "career_switch": False,
                }
            assert anyio.run(persisted) == (1, 0)
            saved = client.put("/me/route-preferences", json=PREFERENCES_REQUEST)
            assert saved.status_code == 200
            assert client.get("/me/route-preferences").json() == saved.json()
            identity.user_id = uuid4()
            assert client.get("/me/route-preferences").status_code == 404
    finally:
        anyio.run(database.dispose)

import anyio
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text

from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import auth_sessions, profiles, users
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

pytest_plugins = ("test_acceptance_persistence",)


async def _suppress_profile(database_url: str) -> None:
    database = Database.create(database_url)
    try:
        async with database.sessions.begin() as session:
            await session.execute(text(
                "CREATE FUNCTION skip_profile() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RETURN NULL; END $$"
            ))
            await session.execute(text(
                "CREATE TRIGGER skip_profile BEFORE INSERT ON profiles "
                "FOR EACH ROW EXECUTE FUNCTION skip_profile()"
            ))
    finally:
        await database.dispose()


async def _user_count(database_url: str) -> int:
    database = Database.create(database_url)
    try:
        async with database.sessions() as session:
            return (await session.execute(select(func.count()).select_from(users))).scalar_one()
    finally:
        await database.dispose()


def test_guest_bootstrap_returns_unavailable_and_rolls_back_incomplete_identity(
    acceptance_database_url: str,
) -> None:
    # Given
    anyio.run(_suppress_profile, acceptance_database_url)
    settings = Settings(_env_file=None, environment="test",
                        database_url=acceptance_database_url, guest_sessions_enabled=True)
    # When
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/auth/session")
    # Then
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"
    assert response.headers["Cache-Control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert anyio.run(_user_count, acceptance_database_url) == 0


async def _remove_profiles(database_url: str) -> None:
    database = Database.create(database_url)
    try:
        async with database.sessions.begin() as session:
            await session.execute(delete(profiles))
    finally:
        await database.dispose()


def test_missing_profile_on_reuse_never_creates_replacement_guest(
    acceptance_database_url: str,
) -> None:
    # Given
    settings = Settings(_env_file=None, environment="test",
                        database_url=acceptance_database_url, guest_sessions_enabled=True)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/auth/session").status_code == 200
        anyio.run(_remove_profiles, acceptance_database_url)
        # When
        response = client.get("/api/v1/auth/session")
    # Then
    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    assert anyio.run(_user_count, acceptance_database_url) == 1


async def _drop_sessions(database_url: str) -> None:
    database = Database.create(database_url)
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(lambda sync: auth_sessions.drop(sync))
    finally:
        await database.dispose()


def test_guest_database_failure_is_a_sanitized_503(acceptance_database_url: str) -> None:
    # Given
    anyio.run(_drop_sessions, acceptance_database_url)
    settings = Settings(_env_file=None, environment="test",
                        database_url=acceptance_database_url, guest_sessions_enabled=True)
    # When
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/auth/session")
    # Then
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert "auth_sessions" not in response.text

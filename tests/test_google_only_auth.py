"""Google accounts are the only source of persisted browser identity."""

from datetime import timedelta
from uuid import uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, insert, select, update

from jobtology_be.infrastructure.persistence.auth_contracts import GoogleLogin
from jobtology_be.infrastructure.persistence.auth_store import PostgresAuthStore
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import auth_sessions, profiles, users
from jobtology_be.main import create_app
from jobtology_be.modules.auth.session import SessionCredentials, issue_session_credentials
from jobtology_be.modules.auth.session_cookies import SESSION_COOKIE_NAME
from jobtology_be.settings import Settings

pytest_plugins = ("test_acceptance_persistence",)

ORIGIN = "http://localhost:5173"
SESSION_PATH = "/api/v1/auth/session"


def _settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None, environment="test", database_url=database_url,
        auth_enabled=True, turnstile_enabled=False,
        google_client_id="client-id", google_client_secret="secret",
        google_redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
        frontend_url=ORIGIN, cors_origins=[ORIGIN],
    )


def test_legacy_guest_flag_is_rejected_even_without_google_credentials() -> None:
    with pytest.raises(ValidationError, match="guest_sessions_enabled"):
        Settings(_env_file=None, guest_sessions_enabled=True)


@pytest.mark.parametrize("value", ["false", "False", "0", "off", "no"])
def test_disabled_guest_environment_flag_is_accepted(monkeypatch, value: str) -> None:
    monkeypatch.setenv("JOBTOLOGY_GUEST_SESSIONS_ENABLED", value)
    assert Settings(_env_file=None).guest_sessions_enabled is False


@pytest.mark.parametrize("value", ["true", "True", "1", "on", "yes"])
def test_enabled_guest_environment_flag_is_rejected(monkeypatch, value: str) -> None:
    monkeypatch.setenv("JOBTOLOGY_GUEST_SESSIONS_ENABLED", value)
    with pytest.raises(ValidationError, match="guest_sessions_enabled"):
        Settings(_env_file=None)


@pytest.mark.parametrize("cookie", [None, "malformed", "unknown.token", "a.b.c", ".token"])
def test_session_requests_never_bootstrap_anonymous_accounts(cookie: str | None) -> None:
    with TestClient(create_app(Settings(_env_file=None, environment="test"))) as client:
        if cookie is not None:
            client.cookies.set(SESSION_COOKIE_NAME, cookie)
        response = client.get(SESSION_PATH)
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_staged_google_urls_leave_authentication_disabled_until_credentials_are_ready() -> None:
    settings = Settings(
        _env_file=None, environment="test", auth_enabled=False,
        google_redirect_uri="https://jobtology.example/api/v1/auth/google/callback",
        frontend_url="https://jobtology.example", cors_origins=["https://jobtology.example"],
    )
    with TestClient(create_app(settings)) as client:
        assert client.get(SESSION_PATH).status_code == 401
        login = client.get("/api/v1/auth/google/login", follow_redirects=False)
        callback = client.get(
            "/api/v1/auth/google/callback?state=unused&code=unused", follow_redirects=False,
        )
    assert login.status_code == callback.status_code == 503
    assert "location" not in login.headers
    assert "location" not in callback.headers


async def _create_legacy_guest(database_url: str) -> SessionCredentials:
    database = Database.create(database_url)
    credentials = issue_session_credentials()
    try:
        # Reproduce an old guest created before Google-only deployment: an ACTIVE
        # user with a live cookie but no Google identity.
        async with database.sessions.begin() as session:
            user_id = uuid4()
            await session.execute(insert(users).values(id=user_id))
            await session.execute(insert(profiles).values(user_id=user_id))
            await session.execute(insert(auth_sessions).values(
                id=uuid4(), user_id=user_id,
                token_hash=credentials.hashes.token_hash,
                csrf_hash=credentials.hashes.csrf_hash,
                expires_at=func.current_timestamp() + timedelta(days=7),
            ))
        store = PostgresAuthStore(database)
        assert await store.resolve_session(credentials.hashes.token_hash) is None
        assert not await store.matches_session_csrf(
            credentials.hashes.token_hash, credentials.hashes.csrf_hash,
        )
        return credentials
    finally:
        await database.dispose()


def test_live_legacy_guest_cookie_cannot_access_google_only_api(
    acceptance_database_url: str,
) -> None:
    credentials = anyio.run(_create_legacy_guest, acceptance_database_url)
    with TestClient(create_app(_settings(acceptance_database_url))) as client:
        client.cookies.set(SESSION_COOKIE_NAME, credentials.cookie_value)
        response = client.get(SESSION_PATH)
        product = client.get("/api/v1/me/profile")
        logout = client.post("/api/v1/auth/logout", headers={
            "Origin": ORIGIN, "X-CSRF-Token": credentials.csrf_token,
        })
    assert response.status_code == product.status_code == logout.status_code == 401
    assert "set-cookie" not in response.headers


async def _create_google_session(database_url: str) -> SessionCredentials:
    database = Database.create(database_url)
    credentials = issue_session_credentials()
    try:
        issued = await PostgresAuthStore(database).create_google_session(GoogleLogin(
            google_subject="verified-google-subject",
            session=credentials.session_issue(timedelta(days=7)),
        ))
        assert issued is not None
        return credentials
    finally:
        await database.dispose()


async def _identity_counts(database_url: str) -> tuple[int, int]:
    database = Database.create(database_url)
    try:
        async with database.sessions() as session:
            return (
                await session.scalar(select(func.count()).select_from(users)),
                await session.scalar(select(func.count()).select_from(auth_sessions)),
            )
    finally:
        await database.dispose()


def test_google_logout_does_not_create_a_replacement_account(
    acceptance_database_url: str,
) -> None:
    credentials = anyio.run(_create_google_session, acceptance_database_url)
    with TestClient(create_app(_settings(acceptance_database_url))) as client:
        client.cookies.set(SESSION_COOKIE_NAME, credentials.cookie_value)
        current = client.get(SESSION_PATH)
        assert current.status_code == 200
        logout = client.post("/api/v1/auth/logout", headers={
            "Origin": ORIGIN, "X-CSRF-Token": current.json()["csrf_token"],
        })
        after_logout = client.get(SESSION_PATH)
        client.cookies.set(SESSION_COOKIE_NAME, credentials.cookie_value)
        replay = client.get(SESSION_PATH)
    assert logout.status_code == 204
    assert after_logout.status_code == replay.status_code == 401
    assert "set-cookie" not in after_logout.headers
    assert anyio.run(_identity_counts, acceptance_database_url) == (1, 1)


async def _deactivate_google_user(database_url: str) -> None:
    database = Database.create(database_url)
    try:
        async with database.sessions.begin() as session:
            await session.execute(update(users).values(status="DEACTIVATED"))
    finally:
        await database.dispose()


def test_deactivated_google_account_cannot_use_an_unexpired_session(
    acceptance_database_url: str,
) -> None:
    credentials = anyio.run(_create_google_session, acceptance_database_url)
    anyio.run(_deactivate_google_user, acceptance_database_url)
    with TestClient(create_app(_settings(acceptance_database_url))) as client:
        client.cookies.set(SESSION_COOKIE_NAME, credentials.cookie_value)
        assert client.get(SESSION_PATH).status_code == 401
        assert client.post("/api/v1/auth/logout", headers={
            "Origin": ORIGIN, "X-CSRF-Token": credentials.csrf_token,
        }).status_code == 401

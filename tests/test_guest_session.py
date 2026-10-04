from uuid import UUID

import anyio
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, text, update

from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    auth_sessions,
    google_identities,
    profiles,
    users,
)
from jobtology_be.main import create_app
from jobtology_be.modules.auth.session_cookies import SESSION_COOKIE_NAME
from jobtology_be.settings import Settings

pytest_plugins = ("test_acceptance_persistence",)

ORIGIN = "http://localhost:5173"
PATH = "/api/v1/auth/session"


def _settings(database_url: str, *, limit: int = 30) -> Settings:
    return Settings(
        _env_file=None, environment="test", database_url=database_url,
        guest_sessions_enabled=True, guest_session_max_new_per_minute=limit,
        cors_origins=[ORIGIN],
    )


async def _counts_and_expire(database_url: str, user_id: UUID) -> tuple[int, int, int]:
    database = Database.create(database_url)
    try:
        async with database.sessions.begin() as session:
            counts = (
                (await session.execute(select(func.count()).select_from(users))).scalar_one(),
                (await session.execute(select(func.count()).select_from(profiles))).scalar_one(),
                (await session.execute(select(func.count()).select_from(google_identities))).scalar_one(),
            )
            await session.execute(
                update(auth_sessions).where(auth_sessions.c.user_id == user_id)
                .values(expires_at=func.clock_timestamp() - text("INTERVAL '1 second'"))
            )
        return counts
    finally:
        await database.dispose()


def test_guest_session_bootstraps_two_isolated_browsers_and_reuses_valid_cookie(
    acceptance_database_url: str,
) -> None:
    # Given
    app = create_app(_settings(acceptance_database_url))

    # When
    with TestClient(app) as first, TestClient(create_app(_settings(acceptance_database_url))) as second:
        assert first.get("/api/v1/me/profile").status_code == 401
        a = first.get(PATH, headers={"Origin": ORIGIN})
        b = second.get(PATH, headers={"Origin": ORIGIN})
        assert a.status_code == b.status_code == 200
        reused = first.get(PATH, headers={"Origin": ORIGIN})
        foreign = first.get("/api/v1/me/profile", cookies=second.cookies)
        missing_csrf = first.put(
            "/api/v1/me/profile", headers={"Origin": ORIGIN},
            json={"expected_profile_version": 1, "major_raw": "Computer Science"},
        )
        wrong_csrf = first.put(
            "/api/v1/me/profile", headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
            json={"expected_profile_version": 1, "major_raw": "Computer Science"},
        )
        updated = first.put(
            "/api/v1/me/profile", headers={"Origin": ORIGIN, "X-CSRF-Token": a.json()["csrf_token"]},
            json={"expected_profile_version": 1, "major_raw": "Computer Science"},
        )
        refreshed = first.get(PATH)
        other_profile = second.get("/api/v1/me/profile")
        counts = anyio.run(_counts_and_expire, acceptance_database_url, UUID(a.json()["user_id"]))
        expired = first.get(PATH)

    # Then
    assert a.status_code == b.status_code == reused.status_code == 200
    assert a.json()["user_id"] != b.json()["user_id"]
    assert a.json() == reused.json()
    assert a.json()["profile_version"] == 1
    assert a.headers["Cache-Control"] == "no-store"
    assert foreign.status_code == 200  # possession of the other browser's cookie is identity
    assert foreign.json()["user_id"] == b.json()["user_id"]
    assert missing_csrf.status_code == wrong_csrf.status_code == 403
    assert updated.status_code == 200
    assert refreshed.json()["profile_version"] == 2
    assert other_profile.json()["profile_version"] == 1
    assert other_profile.json()["major_raw"] == ""
    assert "set-cookie" not in reused.headers
    assert "HttpOnly" in a.headers["set-cookie"]
    assert "SameSite=lax" in a.headers["set-cookie"]
    assert "Path=/" in a.headers["set-cookie"]
    assert "Max-Age=604800" in a.headers["set-cookie"]
    assert counts == (2, 2, 0)
    assert expired.status_code == 200
    assert expired.json()["user_id"] != a.json()["user_id"]


def test_guest_bootstrap_rejects_foreign_origins_and_caps_new_users(
    acceptance_database_url: str,
) -> None:
    # Given
    app = create_app(_settings(acceptance_database_url, limit=1))

    # When
    with TestClient(app) as first, TestClient(create_app(_settings(acceptance_database_url, limit=1))) as second:
        foreign = first.get(PATH, headers={"Origin": "https://attacker.example"})
        cross_site = first.get(PATH, headers={"Sec-Fetch-Site": "cross-site"})
        google = first.get("/api/v1/auth/google/login")
        issued = first.get(PATH)
        reused = first.get(PATH)
        limited = second.get(PATH)
        logged_out = first.post(
            "/api/v1/auth/logout",
            headers={"Origin": ORIGIN, "X-CSRF-Token": issued.json()["csrf_token"]},
        )
        after_logout = first.get("/api/v1/me/profile")
        counts = anyio.run(_counts_and_expire, acceptance_database_url, UUID(issued.json()["user_id"]))

    # Then
    assert foreign.status_code == cross_site.status_code == 403
    assert google.status_code == 503
    assert issued.status_code == reused.status_code == 200
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert counts == (1, 1, 0)
    assert logged_out.status_code == 204
    assert after_logout.status_code == 401


def test_guest_configuration_is_explicit_and_mutually_exclusive() -> None:
    # Given / When / Then
    for values in (
        {"auth_enabled": True, "guest_sessions_enabled": True,
         "google_client_id": "id", "google_client_secret": "secret",
         "google_redirect_uri": "http://localhost:8000/api/v1/auth/google/callback",
         "frontend_url": ORIGIN},
        {"guest_sessions_enabled": True, "guest_session_max_new_per_minute": 0},
        {"guest_sessions_enabled": True, "guest_session_max_new_per_minute": 1001},
    ):
        try:
            Settings(_env_file=None, environment="test", **values)
        except ValidationError:
            continue
        raise AssertionError("Invalid guest configuration accepted")
    assert Settings(_env_file=None, environment="test", guest_sessions_enabled=True,
                    google_client_id="unused-partial").guest_sessions_enabled


@pytest.mark.parametrize("cookie", ["malformed", "unknown.token", "a.b.c", ".token"])
def test_invalid_cookie_bootstraps_a_fresh_guest(
    acceptance_database_url: str, cookie: str,
) -> None:
    # Given
    with TestClient(create_app(_settings(acceptance_database_url))) as client:
        previous = client.get(PATH).json()["user_id"]
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE_NAME, cookie)
        # When
        response = client.get(PATH)
        # Then
        assert response.status_code == 200
        assert response.json()["user_id"] != previous
        assert response.json()["profile_version"] == 1


def test_logout_revokes_old_cookie_and_bootstraps_new_identity(
    acceptance_database_url: str,
) -> None:
    # Given
    with TestClient(create_app(_settings(acceptance_database_url))) as client:
        session = client.get(PATH).json()
        old_cookie = client.cookies.get(SESSION_COOKIE_NAME)
        assert old_cookie is not None
        assert client.post("/api/v1/auth/logout").status_code == 403
        # When
        logout = client.post("/api/v1/auth/logout", headers={
            "Origin": ORIGIN, "X-CSRF-Token": session["csrf_token"],
        })
        # Then
        assert logout.status_code == 204
        assert client.cookies.get(SESSION_COOKIE_NAME) is None
        client.cookies.set(SESSION_COOKIE_NAME, old_cookie)
        assert client.get("/api/v1/me/profile").status_code == 401
        fresh = client.get(PATH)
        assert fresh.status_code == 200
        assert fresh.json()["user_id"] != session["user_id"]


def test_guest_mode_is_disabled_by_default() -> None:
    # Given
    settings = Settings(_env_file=None, environment="test")
    # When
    with TestClient(create_app(settings)) as client:
        response = client.get(PATH)
    # Then
    assert not settings.guest_sessions_enabled
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_guest_production_cookie_is_secure(acceptance_database_url: str) -> None:
    # Given
    settings = _settings(acceptance_database_url)
    settings.environment = "production"
    # When
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        response = client.get(PATH)
    # Then
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]

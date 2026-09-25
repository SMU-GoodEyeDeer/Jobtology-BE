from uuid import UUID

from fastapi.testclient import TestClient

from jobtology_be.api.auth_session import require_session_store
from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import require_authenticated_principal
from jobtology_be.infrastructure.persistence.auth_contracts import SessionPrincipal
from jobtology_be.infrastructure.persistence.auth_store import PostgresAuthStore
from jobtology_be.main import create_app
from jobtology_be.modules.auth.session import SessionCredentials, issue_session_credentials
from jobtology_be.modules.auth.session_cookies import SESSION_COOKIE_NAME
from jobtology_be.settings import Settings

USER_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
FRONTEND_ORIGIN = "http://localhost:5173"


class RecordingSessionStore:
    def __init__(self, credentials: SessionCredentials) -> None:
        self._credentials = credentials
        self.revoked_session_hash: bytes | None = None

    async def resolve_session(self, session_token_hash: bytes) -> SessionPrincipal | None:
        if session_token_hash != self._credentials.hashes.token_hash:
            return None
        return SessionPrincipal(user_id=USER_ID, csrf_hash=self._credentials.hashes.csrf_hash)

    async def matches_session_csrf(self, session_token_hash: bytes, csrf_hash: bytes) -> bool:
        return (
            session_token_hash == self._credentials.hashes.token_hash
            and csrf_hash == self._credentials.hashes.csrf_hash
        )

    async def revoke_session(self, session_token_hash: bytes) -> None:
        self.revoked_session_hash = session_token_hash


def _application(store: RecordingSessionStore):
    return create_app(
        Settings(_env_file=None, environment="test", cors_origins=[FRONTEND_ORIGIN]),
        dependencies=ApiDependencies(session_store=store),
    )


def _session_cookie(credentials: SessionCredentials) -> dict[str, str]:
    return {SESSION_COOKIE_NAME: credentials.cookie_value}


def test_session_endpoint_returns_the_session_bound_csrf_token() -> None:
    # Given
    credentials = issue_session_credentials()
    app = _application(RecordingSessionStore(credentials))

    # When
    with TestClient(app) as client:
        response = client.get("/api/v1/auth/session", cookies=_session_cookie(credentials))

    # Then
    assert response.status_code == 200
    assert response.json() == {"user_id": str(USER_ID), "csrf_token": credentials.csrf_token}
    assert credentials.session_token not in response.text


def test_logout_requires_csrf_and_revokes_the_server_session() -> None:
    # Given
    credentials = issue_session_credentials()
    store = RecordingSessionStore(credentials)
    app = _application(store)

    # When
    with TestClient(app) as client:
        rejected = client.post(
            "/api/v1/auth/logout",
            cookies=_session_cookie(credentials),
            headers={"Origin": FRONTEND_ORIGIN},
        )
        response = client.post(
            "/api/v1/auth/logout",
            cookies=_session_cookie(credentials),
            headers={"Origin": FRONTEND_ORIGIN, "X-CSRF-Token": credentials.csrf_token},
        )

    # Then
    assert rejected.status_code == 403
    assert response.status_code == 204
    assert store.revoked_session_hash == credentials.hashes.token_hash
    assert "jobtology_session=\"\"" in response.headers["set-cookie"]


def test_logout_rejects_an_untrusted_origin_even_with_the_csrf_token() -> None:
    # Given
    credentials = issue_session_credentials()
    app = _application(RecordingSessionStore(credentials))

    # When
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/logout",
            cookies=_session_cookie(credentials),
            headers={"Origin": "https://attacker.example", "X-CSRF-Token": credentials.csrf_token},
        )

    # Then
    assert response.status_code == 403


def test_unsafe_product_request_requires_the_session_csrf_token() -> None:
    # Given
    credentials = issue_session_credentials()
    app = _application(RecordingSessionStore(credentials))
    request_body = {"expected_profile_version": 1, "major_raw": "Computer Science"}

    # When
    with TestClient(app) as client:
        rejected = client.put(
            "/api/v1/me/profile",
            cookies=_session_cookie(credentials),
            headers={"Origin": FRONTEND_ORIGIN},
            json=request_body,
        )
        authenticated = client.put(
            "/api/v1/me/profile",
            cookies=_session_cookie(credentials),
            headers={"Origin": FRONTEND_ORIGIN, "X-CSRF-Token": credentials.csrf_token},
            json=request_body,
        )

    # Then
    assert rejected.status_code == 403
    assert authenticated.status_code == 503


def test_session_identity_does_not_accept_a_request_supplied_user_header() -> None:
    # Given
    credentials = issue_session_credentials()
    app = _application(RecordingSessionStore(credentials))

    # When
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/session",
            headers={"X-User-ID": str(USER_ID)},
        )

    # Then
    assert response.status_code == 401


def test_enabled_auth_composes_the_postgres_session_store() -> None:
    # Given
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+asyncpg://jobtology:secret@localhost:5432/jobtology",
        auth_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
        frontend_url=FRONTEND_ORIGIN,
        cors_origins=[FRONTEND_ORIGIN],
    )

    # When
    app = create_app(settings)

    # Then
    session_store_provider = app.dependency_overrides[require_session_store]
    assert isinstance(session_store_provider(), PostgresAuthStore)
    assert require_authenticated_principal in app.dependency_overrides


def test_enabled_auth_without_database_or_fixture_store_fails_closed() -> None:
    # Given
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
        frontend_url=FRONTEND_ORIGIN,
        cors_origins=[FRONTEND_ORIGIN],
    )

    # When / Then
    try:
        _ = create_app(settings)
    except ValueError as error:
        assert str(error) == "Enabled authentication requires a database URL or injected session store"
    else:
        raise AssertionError("Enabled authentication unexpectedly composed without a session store")

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import anyio
import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.types import Message, Scope

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.infrastructure.persistence.auth_contracts import (
    ConsumedOAuthLoginAttempt,
    GoogleLogin,
    IssuedSession,
    OAuthLoginAttempt,
    SessionPrincipal,
)
from jobtology_be.main import create_app
from jobtology_be.modules.auth.google_oidc import (
    GoogleIdentityProvider,
    GoogleOidcProvider,
    GoogleProviderError,
)
from jobtology_be.modules.auth.session_cookies import (
    OAUTH_ATTEMPT_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    OAuthCallbackQueryMiddleware,
)
from jobtology_be.settings import GoogleOidcSettings, Settings

USER_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
CALLBACK = "/api/v1/auth/google/callback"


class FakeStore:
    def __init__(self) -> None:
        self.attempts: dict[bytes, OAuthLoginAttempt] = {}
        self.subjects: list[str] = []
        self.sessions: dict[bytes, SessionPrincipal] = {}

    async def create_oauth_login_attempt(self, attempt: OAuthLoginAttempt) -> None:
        self.attempts[attempt.state_hash] = attempt

    async def consume_oauth_login_attempt(self, state_hash: bytes, browser_binding_hash: bytes) -> ConsumedOAuthLoginAttempt | None:
        attempt = self.attempts.get(state_hash)
        if attempt is None or attempt.browser_binding_hash != browser_binding_hash:
            return None
        del self.attempts[state_hash]
        return ConsumedOAuthLoginAttempt(attempt.nonce, attempt.pkce_verifier)

    async def create_google_session(self, login: GoogleLogin) -> IssuedSession:
        self.subjects.append(login.google_subject)
        self.sessions[login.session.token_hash] = SessionPrincipal(USER_ID, login.session.csrf_hash)
        return IssuedSession(USER_ID, datetime.now(UTC))

    async def resolve_session(self, session_token_hash: bytes) -> SessionPrincipal | None:
        return self.sessions.get(session_token_hash)

    async def matches_session_csrf(self, session_token_hash: bytes, csrf_hash: bytes) -> bool:
        session = self.sessions.get(session_token_hash)
        return session is not None and session.csrf_hash == csrf_hash

    async def revoke_session(self, session_token_hash: bytes) -> None:
        _ = self.sessions.pop(session_token_hash, None)


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def subject_for_code(self, code: str, verifier: str, nonce: str) -> str:
        self.calls.append((code, verifier, nonce))
        return "verified-google-subject"


def _client(store: FakeStore, provider: GoogleIdentityProvider, *, enabled: bool = True) -> TestClient:
    return TestClient(create_app(Settings.model_validate({
        "environment": "test", "auth_enabled": enabled,
        "google_client_id": "client-id", "google_client_secret": "secret",
        "google_redirect_uri": "http://localhost:8000/api/v1/auth/google/callback",
        "frontend_url": "http://localhost:5173", "cors_origins": ["http://localhost:5173"],
    }), dependencies=ApiDependencies(session_store=store, google_login_store=store,
                                    google_identity_provider=provider)))


def test_google_login_callback_issues_session_from_verified_subject() -> None:
    store, provider = FakeStore(), FakeProvider()
    with _client(store, provider) as client:
        login = client.get("/api/v1/auth/google/login", follow_redirects=False)
        assert login.status_code == 307
        query = parse_qs(urlsplit(login.headers["location"]).query)
        assert urlsplit(login.headers["location"]).hostname == "accounts.google.com"
        assert query["scope"] == ["openid"]
        assert query["code_challenge_method"] == ["S256"]
        assert OAUTH_ATTEMPT_COOKIE_NAME in client.cookies
        attempt_cookie = login.headers["set-cookie"].lower()
        assert "httponly" in attempt_cookie
        assert "samesite=lax" in attempt_cookie
        assert "max-age=600" in attempt_cookie
        assert "path=/api/v1/auth/google" in attempt_cookie
        callback = client.get(CALLBACK, params={"code": "code", "state": query["state"][0]},
                              follow_redirects=False)
        session = client.get("/api/v1/auth/session")
        rejected_logout = client.post("/api/v1/auth/logout", headers={"Origin": "http://localhost:5173"})
        logout = client.post("/api/v1/auth/logout", headers={
            "Origin": "http://localhost:5173", "X-CSRF-Token": session.json()["csrf_token"],
        })
        replay = client.get(CALLBACK, params={"code": "code", "state": query["state"][0]},
                            follow_redirects=False)
    assert callback.status_code == 307
    assert callback.headers["location"] == "http://localhost:5173"
    assert SESSION_COOKIE_NAME in callback.headers["set-cookie"]
    assert "path=/; samesite=lax" in callback.headers["set-cookie"].lower()
    assert callback.headers["cache-control"] == "no-store"
    assert session.status_code == 200
    assert session.json()["user_id"] == str(USER_ID)
    assert session.json()["csrf_token"]
    assert rejected_logout.status_code == 403
    assert logout.status_code == 204
    assert store.subjects == ["verified-google-subject"]
    assert provider.calls[0][0] == "code"
    assert replay.status_code == 400


def test_google_callback_requires_original_browser_binding() -> None:
    store, provider = FakeStore(), FakeProvider()
    with _client(store, provider) as client:
        login = client.get("/api/v1/auth/google/login", follow_redirects=False)
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        client.cookies.clear()
        rejected = client.get(CALLBACK, params={"code": "code", "state": state})
    assert rejected.status_code == 400
    assert not provider.calls
    assert not store.subjects


def test_disabled_auth_does_not_create_attempt() -> None:
    store, provider = FakeStore(), FakeProvider()
    with _client(store, provider, enabled=False) as client:
        response = client.get("/api/v1/auth/google/login")
    assert response.status_code == 503
    assert not store.attempts


def test_google_denial_consumes_attempt_and_clears_cookie() -> None:
    store, provider = FakeStore(), FakeProvider()
    with _client(store, provider) as client:
        login = client.get("/api/v1/auth/google/login", follow_redirects=False)
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        denied = client.get(CALLBACK, params={"error": "access_denied", "state": state})
    assert denied.status_code == 400
    assert denied.headers["cache-control"] == "no-store"
    assert OAUTH_ATTEMPT_COOKIE_NAME not in client.cookies
    assert not store.attempts
    assert not provider.calls


def test_provider_failure_never_issues_session() -> None:
    class BrokenProvider:
        async def subject_for_code(self, code: str, verifier: str, nonce: str) -> str:
            raise GoogleProviderError

    store = FakeStore()
    with _client(store, BrokenProvider()) as client:
        login = client.get("/api/v1/auth/google/login", follow_redirects=False)
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        failure = client.get(CALLBACK, params={"code": "sensitive-code", "state": state})
    assert failure.status_code == 400
    assert "sensitive-code" not in failure.text
    assert SESSION_COOKIE_NAME not in failure.headers.get("set-cookie", "")
    assert OAUTH_ATTEMPT_COOKIE_NAME not in client.cookies
    assert not store.subjects


def test_duplicate_callback_parameters_are_rejected() -> None:
    store, provider = FakeStore(), FakeProvider()
    with _client(store, provider) as client:
        login = client.get("/api/v1/auth/google/login", follow_redirects=False)
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        rejected = client.get(f"{CALLBACK}?code=one&code=two&state={state}")
    assert rejected.status_code == 400
    assert not provider.calls
    assert not store.subjects


def test_uvicorn_access_path_does_not_include_callback_secrets() -> None:
    scope: Scope = {
        "type": "http", "path": CALLBACK,
        "query_string": b"code=secret-code&state=secret-state", "state": {},
    }

    async def inner(scope: Scope, receive, send) -> None:
        assert scope["state"]["oauth_callback_query"]["code"] == ["secret-code"]
        assert scope["state"]["oauth_callback_query"]["state"] == ["secret-state"]

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        return None

    anyio.run(OAuthCallbackQueryMiddleware(inner), scope, receive, send)
    assert scope["query_string"] == b""


def test_signed_provider_token_creates_normal_browser_session() -> None:
    signing_key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "fixture"})
    store = FakeStore()

    def google_response(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            assert store.attempts == {}
            nonce = next_nonce[0]
            now = int(datetime.now(UTC).timestamp())
            token = JsonWebToken(["RS256"]).encode(
                {"alg": "RS256", "kid": "fixture"},
                {"iss": "https://accounts.google.com", "aud": "client-id", "sub": "signed-sub",
                 "nonce": nonce, "exp": now + 300, "iat": now}, signing_key,
            ).decode()
            return httpx.Response(200, json={"id_token": token})
        return httpx.Response(200, json={"keys": [signing_key.as_dict(is_private=False)]})

    next_nonce: list[str] = []
    provider = GoogleOidcProvider(
        GoogleOidcSettings("client-id", SecretStr("secret"),
                           "http://localhost:8000/api/v1/auth/google/callback", "http://localhost:5173"),
        transport=httpx.MockTransport(google_response),
    )
    with _client(store, provider) as client:
        login = client.get("/api/v1/auth/google/login", follow_redirects=False)
        query = parse_qs(urlsplit(login.headers["location"]).query)
        next_nonce.append(query["nonce"][0])
        callback = client.get(CALLBACK, params={"code": "code", "state": query["state"][0]},
                              follow_redirects=False)
        session = client.get("/api/v1/auth/session")
    assert callback.status_code == 307
    assert store.subjects == ["signed-sub"]
    assert session.status_code == 200
    assert session.json()["user_id"] == str(USER_ID)

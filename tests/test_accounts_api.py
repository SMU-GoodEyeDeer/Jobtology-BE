from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.infrastructure.persistence.auth_contracts import SessionPrincipal
from jobtology_be.main import create_app
from jobtology_be.modules.auth.session import issue_session_credentials
from jobtology_be.modules.auth.session_cookies import SESSION_COOKIE_NAME
from jobtology_be.settings import Settings

ORIGIN = "http://localhost:5173"


class AccountFixture:
    def __init__(self):
        self.user_id = uuid4()
        self.credentials = issue_session_credentials()
        self.deleted = []

    async def resolve_session(self, token):
        if token != self.credentials.hashes.token_hash or self.deleted:
            return None
        return SessionPrincipal(self.user_id, self.credentials.hashes.csrf_hash, "Google Name")

    async def matches_session_csrf(self, token, csrf):
        return (
            token == self.credentials.hashes.token_hash
            and csrf == self.credentials.hashes.csrf_hash
        )

    async def revoke_session(self, token):
        raise AssertionError("Account deletion must remove all sessions in its transaction")

    async def delete_account(self, user_id):
        self.deleted.append(user_id)


def _client(fixture):
    return TestClient(
        create_app(
            Settings(_env_file=None, environment="test", cors_origins=[ORIGIN]),
            dependencies=ApiDependencies(session_store=fixture, account_service=fixture),
        )
    )


@pytest.mark.parametrize(
    "mode,expected", [("no_session", 401), ("no_csrf", 403), ("bad_origin", 403)]
)
def test_delete_requires_session_csrf_and_trusted_origin(mode, expected):
    fixture = AccountFixture()
    with _client(fixture) as client:
        headers = {"Origin": ORIGIN, "X-CSRF-Token": fixture.credentials.csrf_token}
        if mode != "no_session":
            client.cookies.set(SESSION_COOKIE_NAME, fixture.credentials.cookie_value)
        if mode == "no_csrf":
            del headers["X-CSRF-Token"]
        if mode == "bad_origin":
            headers["Origin"] = "https://attacker.example"
        response = client.delete("/api/v1/me/account", headers=headers)
    assert response.status_code == expected
    assert fixture.deleted == []


def test_delete_uses_authenticated_user_clears_cookies_and_invalidates_session():
    fixture = AccountFixture()
    with _client(fixture) as client:
        client.cookies.set(SESSION_COOKIE_NAME, fixture.credentials.cookie_value)
        assert client.get("/api/v1/auth/session").json()["display_name"] == "Google Name"
        response = client.delete(
            "/api/v1/me/account",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": fixture.credentials.csrf_token,
                "X-User-ID": str(uuid4()),
            },
        )
        assert client.get("/api/v1/auth/session").status_code == 401
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert any(
        'jobtology_session=""' in cookie for cookie in response.headers.get_list("set-cookie")
    )
    assert any(
        'jobtology_oauth_attempt=""' in cookie for cookie in response.headers.get_list("set-cookie")
    )
    assert fixture.deleted == [fixture.user_id]

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_accounts_api import AccountFixture

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.main import create_app
from jobtology_be.modules.auth.session_cookies import SESSION_COOKIE_NAME
from jobtology_be.settings import Settings

ORIGIN = "http://localhost:5173"


class GoalFixture(AccountFixture):
    def __init__(self):
        super().__init__()
        self.goal_deletions = []

    async def delete_goal(self, user_id, goal_id, expected_profile_version):
        self.goal_deletions.append((user_id, goal_id, expected_profile_version))
        return expected_profile_version + 1


def client_for(fixture):
    return TestClient(create_app(
        Settings(_env_file=None, environment="test", cors_origins=[ORIGIN]),
        dependencies=ApiDependencies(session_store=fixture, goal_service=fixture),
    ))


@pytest.mark.parametrize("mode,expected", [("no_session", 401), ("no_csrf", 403), ("bad_origin", 403)])
def test_goal_delete_requires_session_csrf_and_trusted_origin(mode, expected):
    fixture = GoalFixture()
    with client_for(fixture) as client:
        headers = {"Origin": ORIGIN, "X-CSRF-Token": fixture.credentials.csrf_token}
        if mode != "no_session":
            client.cookies.set(SESSION_COOKIE_NAME, fixture.credentials.cookie_value)
        if mode == "no_csrf":
            headers.pop("X-CSRF-Token")
        if mode == "bad_origin":
            headers["Origin"] = "https://attacker.example"
        response = client.request("DELETE", f"/api/v1/me/goals/{uuid4()}",
                                  json={"expected_profile_version": 7}, headers=headers)
    assert response.status_code == expected
    assert fixture.goal_deletions == []


def test_goal_delete_uses_authenticated_owner_and_keeps_the_account_session():
    fixture, goal_id = GoalFixture(), uuid4()
    with client_for(fixture) as client:
        client.cookies.set(SESSION_COOKIE_NAME, fixture.credentials.cookie_value)
        response = client.request("DELETE", f"/api/v1/me/goals/{goal_id}",
                                  json={"expected_profile_version": 7}, headers={
                                      "Origin": ORIGIN, "X-CSRF-Token": fixture.credentials.csrf_token,
                                      "X-User-ID": str(uuid4()),
                                  })
        assert client.get("/api/v1/auth/session").status_code == 200
    assert response.status_code == 200
    assert response.json() == {"goal_id": str(goal_id), "profile_version": 8}
    assert fixture.goal_deletions == [(fixture.user_id, goal_id, 7)]
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("payload", [
    {}, {"expected_profile_version": True}, {"expected_profile_version": 0},
    {"expected_profile_version": "7"}, {"expected_profile_version": 7, "user_id": str(uuid4())},
])
def test_goal_delete_requires_a_strict_positive_profile_version(payload):
    fixture = GoalFixture()
    with client_for(fixture) as client:
        client.cookies.set(SESSION_COOKIE_NAME, fixture.credentials.cookie_value)
        response = client.request("DELETE", f"/api/v1/me/goals/{uuid4()}", json=payload,
                                  headers={"Origin": ORIGIN, "X-CSRF-Token": fixture.credentials.csrf_token})
    assert response.status_code == 422
    assert fixture.goal_deletions == []

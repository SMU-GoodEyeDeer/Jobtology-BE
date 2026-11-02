from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from jobtology_be.api.management import require_management_store
from jobtology_be.infrastructure.persistence.management import (
    ManagementConflict,
    ManagementNotFound,
)
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

TOKEN = "fixture-management-secret-at-least-32-characters"


class FakeManagement:
    def __init__(self):
        self.writes = []

    async def invitations(self, limit, offset):
        return {"items": [], "total": 0}

    async def create_invitation(self, values):
        self.writes.append(values)
        return {
            "invitation": {"id": str(uuid4()), **values},
            "code": "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF",
        }

    async def update_user(self, identifier, values):
        self.writes.append(values)

    async def revoke_invitation(self, identifier):
        raise ManagementNotFound

    async def update_invitation(self, identifier, values):
        raise ManagementConflict("Usage limit cannot be lower than redeemed accounts")


def client_for(store=None, **settings):
    app = create_app(
        Settings(
            environment="test",
            frontend_url="http://localhost:5173",
            management_token=TOKEN,
            **settings,
        )
    )
    if store:
        app.dependency_overrides[require_management_store] = lambda: store
    return TestClient(app)


@pytest.mark.parametrize("authorization", [None, "Bearer bad", "Basic " + TOKEN])
def test_private_management_requires_its_own_token(authorization):
    with client_for(FakeManagement()) as client:
        result = client.get(
            "/internal/admin/invitations",
            headers={"Authorization": authorization} if authorization else {},
        )
        assert result.status_code == 401
        assert TOKEN not in result.text
        assert "/internal/admin/" not in client.get("/api/openapi.json").text


def test_unconfigured_management_is_disabled():
    app = create_app(Settings(environment="test"))
    with TestClient(app) as client:
        assert client.get("/internal/admin/invitations").status_code == 503


def test_invitation_creation_returns_fragment_link_and_preserves_configuration():
    store = FakeManagement()
    with client_for(store) as client:
        response = client.post(
            "/internal/admin/invitations",
            headers={"Authorization": "Bearer " + TOKEN},
            json={"label": "  pilot  ", "access_hours": 0.5, "max_uses": 10, "expires_at": None},
        )
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["invite_url"].startswith("http://localhost:5173/#invite=")
        assert store.writes == [
            {"label": "pilot", "access_hours": 0.5, "max_uses": 10, "expires_at": None}
        ]


@pytest.mark.parametrize(
    "payload",
    [
        {"label": ""},
        {"label": "x", "access_hours": 0},
        {"label": "x", "access_hours": -1},
        {"label": "x", "max_uses": True},
        {"label": "x", "expires_at": "2026-12-01T00:00:00"},
        {"label": "x", "code": "custom"},
    ],
)
def test_invalid_invitation_settings_cannot_write(payload):
    store = FakeManagement()
    with client_for(store) as client:
        assert (
            client.post(
                "/internal/admin/invitations",
                headers={"Authorization": "Bearer " + TOKEN},
                json=payload,
            ).status_code
            == 422
        )
        assert not store.writes


def test_patch_does_not_reset_omitted_expiry_and_errors_are_bounded():
    store = FakeManagement()
    with client_for(store) as client:
        headers = {"Authorization": "Bearer " + TOKEN}
        assert (
            client.patch(
                f"/internal/admin/users/{uuid4()}", headers=headers, json={"status": "ACTIVE"}
            ).status_code
            == 200
        )
        assert store.writes == [{"status": "ACTIVE"}]
        assert (
            client.patch(
                f"/internal/admin/users/{uuid4()}", headers=headers, json={"status": None}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/internal/admin/invitations/{uuid4()}/revoke", headers=headers
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"/internal/admin/invitations/{uuid4()}", headers=headers, json={"max_uses": 1}
            ).status_code
            == 409
        )

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from jobtology_be.main import create_app
from jobtology_be.settings import Settings

pytest_plugins = ("test_acceptance_persistence",)


def test_unused_google_configuration_does_not_affect_guest_mode() -> None:
    # Given / When
    settings = Settings(
        _env_file=None, guest_sessions_enabled=True,
        google_redirect_uri="unused", frontend_url="unused",
    )
    # Then
    assert not settings.auth_enabled
    assert not settings.session_cookie_secure


def test_partial_google_configuration_is_still_rejected_outside_guest_mode() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        Settings(_env_file=None, google_client_id="partial")


def test_guest_mode_requires_a_database() -> None:
    # Given
    settings = Settings(_env_file=None, guest_sessions_enabled=True)
    # When / Then
    with pytest.raises(ValueError, match="database URL"):
        create_app(settings)


def test_google_routes_remain_unavailable_with_partial_guest_configuration(
    acceptance_database_url: str,
) -> None:
    # Given
    settings = Settings(_env_file=None, environment="test", guest_sessions_enabled=True,
                        database_url=acceptance_database_url, google_client_id="unused")
    # When
    with TestClient(create_app(settings)) as client:
        login = client.get("/api/v1/auth/google/login", follow_redirects=False)
        callback = client.get("/api/v1/auth/google/callback?code=unused&state=unused",
                              follow_redirects=False)
    # Then
    assert login.status_code == callback.status_code == 503
    assert "location" not in login.headers
    assert "location" not in callback.headers

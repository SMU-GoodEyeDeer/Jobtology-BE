import pytest
from pydantic import ValidationError

from jobtology_be.settings import Settings


def test_auth_settings_reject_partial_google_configuration() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match="Google OIDC"):
        Settings(
            _env_file=None,
            google_client_id="client-id",
        )


def test_enabled_auth_requires_fixed_google_and_frontend_configuration() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match="Authentication requires"):
        Settings(
            _env_file=None,
            auth_enabled=True,
            google_client_id="client-id",
            google_client_secret="client-secret",
            google_redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
        )


def test_cookie_policy_is_insecure_only_for_local_development() -> None:
    # Given
    local_development = Settings(
        _env_file=None,
        auth_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
        frontend_url="http://localhost:5173",
    )
    production = Settings(
        _env_file=None,
        environment="production",
        database_url="postgresql+asyncpg://jobtology:secret@localhost:5432/jobtology",
        auth_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_redirect_uri="https://api.example.test/api/v1/auth/google/callback",
        frontend_url="https://app.example.test",
        cors_origins=["https://app.example.test"],
    )

    # When / Then
    assert not local_development.session_cookie_secure
    assert production.session_cookie_secure


def test_auth_settings_reject_wildcard_credential_origins() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match="wildcard"):
        Settings(_env_file=None, cors_origins=["*"])

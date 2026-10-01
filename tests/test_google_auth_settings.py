import pytest
from pydantic import SecretStr, ValidationError

from jobtology_be.settings import GoogleOidcSettings, Settings


@pytest.mark.parametrize("redirect, frontend", [
    ("https://api.example.test/other", "https://app.example.test"),
    ("http://api.example.test/api/v1/auth/google/callback", "https://app.example.test"),
    ("https://api.example.test/api/v1/auth/google/callback", "http://app.example.test"),
])
def test_production_auth_rejects_incorrect_callback_or_insecure_frontend(
    redirect: str, frontend: str
) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({
            "environment": "production", "auth_enabled": True,
            "database_url": "postgresql+asyncpg://app:secret@localhost/jobtology",
            "google_client_id": "client-id", "google_client_secret": "secret",
            "google_redirect_uri": redirect, "frontend_url": frontend, "cors_origins": [frontend],
        })


@pytest.mark.parametrize("client_id, client_secret", [
    ("   ", "secret"), ("client-id", "  "), ("", "secret"),
])
def test_blank_google_client_credentials_are_not_configured(
    client_id: str, client_secret: str
) -> None:
    config = GoogleOidcSettings(
        client_id, SecretStr(client_secret),
        "https://api.example.test/api/v1/auth/google/callback", "https://app.example.test",
    )
    assert not config.is_configured


@pytest.mark.parametrize("client_id, client_secret", [
    ("   ", "secret"), ("client-id", "  "),
])
def test_enabled_auth_rejects_blank_google_client_credentials(
    client_id: str, client_secret: str
) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({
            "environment": "production", "auth_enabled": True,
            "database_url": "postgresql+asyncpg://app:secret@localhost/jobtology",
            "google_client_id": client_id, "google_client_secret": client_secret,
            "google_redirect_uri": "https://api.example.test/api/v1/auth/google/callback",
            "frontend_url": "https://app.example.test",
            "cors_origins": ["https://app.example.test"],
        })

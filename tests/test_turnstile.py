import anyio
import httpx
import pytest
from pydantic import SecretStr, ValidationError

from jobtology_be.modules.auth.turnstile import CloudflareTurnstileVerifier
from jobtology_be.settings import Settings


@pytest.mark.parametrize("payload, expected", [
    ({"success": True, "action": "google_login", "hostname": "app.example.test"}, True),
    ({"success": 1, "action": "google_login", "hostname": "app.example.test"}, False),
    ({"success": True, "action": "signup", "hostname": "app.example.test"}, False),
    ({"success": True, "action": "google_login", "hostname": "attacker.example"}, False),
    ({"success": True, "action": "google_login", "hostname": "app.example.test.attacker.example"}, False),
    ({"success": True, "action": "google_login", "hostname": ["app.example.test"]}, False),
    ({"success": False, "error-codes": ["timeout-or-duplicate"]}, False),
    ({"success": True}, False), ({}, False), ([], False), (None, False),
])
def test_siteverify_requires_exact_success_action_and_hostname(payload, expected) -> None:
    verifier = CloudflareTurnstileVerifier(SecretStr("fixture-secret"), ["app.example.test"],
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    assert anyio.run(verifier.verify, "browser-token") is expected


@pytest.mark.parametrize("failure", ["http", "json", "timeout", "deadline", "network"])
def test_siteverify_fails_closed_without_exposing_tokens_or_secrets(failure) -> None:
    def response(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if failure == "deadline":
            raise TimeoutError("fixture wall-clock deadline")
        if failure == "network":
            raise httpx.ConnectError("fixture failure", request=request)
        return httpx.Response(503 if failure == "http" else 200, text="not JSON")
    verifier = CloudflareTurnstileVerifier(SecretStr("fixture-secret"), ["app.example.test"],
        transport=httpx.MockTransport(response))
    assert anyio.run(verifier.verify, "browser-token") is False


@pytest.mark.parametrize("token, hosts", [("", ["app.example.test"]), ("  ", ["app.example.test"]),
                                           ("x" * 2049, ["app.example.test"]), ("token", [])])
def test_invalid_local_inputs_never_call_siteverify(token, hosts) -> None:
    def unexpected(request):
        raise AssertionError("Must reject before network access")
    verifier = CloudflareTurnstileVerifier(SecretStr("fixture-secret"), hosts,
                                         transport=httpx.MockTransport(unexpected))
    assert anyio.run(verifier.verify, token) is False


def configured(**overrides):
    return Settings(_env_file=None, **({
        "environment": "production", "auth_enabled": True,
        "database_url": "postgresql+asyncpg://app:fixture@localhost/jobtology",
        "google_client_id": "client", "google_client_secret": "fixture",
        "google_redirect_uri": "https://api.example.test/api/v1/auth/google/callback",
        "frontend_url": "https://app.example.test", "cors_origins": ["https://app.example.test"],
        "turnstile_secret": "fixture-secret", "turnstile_hostnames": ["app.example.test"],
    } | overrides))


@pytest.mark.parametrize("overrides", [
    {"turnstile_secret": None}, {"turnstile_secret": "  "}, {"turnstile_hostnames": []},
    {"turnstile_hostnames": ["app.example.test", "localhost"]},
    {"turnstile_hostnames": ["app.example.test", "127.0.0.2"]},
    {"turnstile_hostnames": ["app.example.test", "*.example.test"]},
    {"turnstile_hostnames": ["app.example.test:443"]},
    {"turnstile_hostnames": ["https://app.example.test"]},
    {"turnstile_hostnames": ["other.example.test"]},
    {"turnstile_hostnames": ["app.example.test", "some host"]},
])
def test_protected_auth_rejects_incomplete_or_unsafe_configuration(overrides) -> None:
    with pytest.raises(ValidationError, match="Turnstile"):
        configured(**overrides)


def test_turnstile_is_enabled_by_default_and_secret_is_redacted() -> None:
    settings = configured()
    assert settings.turnstile_enabled is True
    assert "fixture-secret" not in repr(settings)

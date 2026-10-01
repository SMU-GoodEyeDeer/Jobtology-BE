from datetime import UTC, datetime

import anyio
import httpx
import pytest
from authlib.jose import JsonWebKey, JsonWebToken
from pydantic import SecretStr

from jobtology_be.modules.auth.google_oidc import GoogleOidcProvider, GoogleProviderError
from jobtology_be.settings import GoogleOidcSettings

CLIENT_ID = "test-client.apps.googleusercontent.com"
NONCE = "expected-nonce"
JWT = JsonWebToken(["RS256"])


@pytest.fixture
def keys():
    private = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "test-key"})
    public = private.as_dict(is_private=False)
    return private, public


def _token(private, **overrides):
    now = int(datetime.now(UTC).timestamp())
    claims = {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "google-subject",
        "nonce": NONCE, "exp": now + 300, "iat": now,
    }
    claims.update(overrides)
    return JWT.encode({"alg": "RS256", "kid": "test-key"}, claims, private).decode()


def _provider(token: str, public, *, status: int = 200) -> GoogleOidcProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(status, json={"id_token": token})
        return httpx.Response(200, json={"keys": [public]})

    return GoogleOidcProvider(
        GoogleOidcSettings(CLIENT_ID, SecretStr("secret"),
                           "http://localhost:8000/api/v1/auth/google/callback", "http://localhost:5173"),
        transport=httpx.MockTransport(handler),
    )


def test_signed_google_token_yields_only_immutable_subject(keys) -> None:
    private, public = keys
    assert anyio.run(_provider(_token(private), public).subject_for_code, "code", "verifier", NONCE) == "google-subject"


@pytest.mark.parametrize("override", [
    {"iss": "https://evil.example"}, {"aud": "other-client"},
    {"nonce": "wrong"}, {"exp": 1}, {"iat": 9999999999}, {"sub": ""},
    {"aud": [CLIENT_ID, "another-client"]},
    {"aud": [CLIENT_ID, "another-client"], "azp": "another-client"},
    {"sub": "   "},
])
def test_untrusted_claims_are_rejected(keys, override) -> None:
    private, public = keys
    with pytest.raises(GoogleProviderError):
        anyio.run(_provider(_token(private, **override), public).subject_for_code, "code", "verifier", NONCE)


@pytest.mark.parametrize("missing", ["iss", "aud", "nonce", "exp", "iat", "sub"])
def test_missing_required_claim_is_rejected(keys, missing) -> None:
    private, public = keys
    now = int(datetime.now(UTC).timestamp())
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "subject",
              "nonce": NONCE, "exp": now + 300, "iat": now}
    del claims[missing]
    token = JWT.encode({"alg": "RS256", "kid": "test-key"}, claims, private).decode()
    with pytest.raises(GoogleProviderError):
        anyio.run(_provider(token, public).subject_for_code, "code", "verifier", NONCE)


def test_wrong_signing_key_is_rejected(keys) -> None:
    private, _ = keys
    other = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "test-key"})
    with pytest.raises(GoogleProviderError):
        anyio.run(_provider(_token(private), other.as_dict(is_private=False)).subject_for_code,
                  "code", "verifier", NONCE)


def test_non_rs256_algorithm_is_rejected(keys) -> None:
    _, public = keys
    token = JsonWebToken(["HS256"]).encode({"alg": "HS256"}, {"sub": "subject"}, b"secret").decode()
    with pytest.raises(GoogleProviderError):
        anyio.run(_provider(token, public).subject_for_code, "code", "verifier", NONCE)


def test_provider_network_failure_is_closed_without_identity() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    provider = GoogleOidcProvider(
        GoogleOidcSettings(CLIENT_ID, SecretStr("secret"),
                           "http://localhost:8000/api/v1/auth/google/callback", "http://localhost:5173"),
        transport=httpx.MockTransport(fail),
    )
    with pytest.raises(GoogleProviderError):
        anyio.run(provider.subject_for_code, "code", "verifier", NONCE)


def test_google_exchange_only_uses_fixed_provider_urls_and_pkce(keys) -> None:
    private, public = keys
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"id_token": _token(private)})
        return httpx.Response(200, json={"keys": [public]})

    provider = GoogleOidcProvider(
        GoogleOidcSettings(CLIENT_ID, SecretStr("secret"),
                           "http://localhost:8000/api/v1/auth/google/callback", "http://localhost:5173"),
        transport=httpx.MockTransport(handler),
    )
    assert anyio.run(provider.subject_for_code, "code", "verifier", NONCE) == "google-subject"
    assert [str(request.url) for request in requests] == [
        "https://oauth2.googleapis.com/token", "https://www.googleapis.com/oauth2/v3/certs",
    ]
    assert b"code_verifier=verifier" in requests[0].content

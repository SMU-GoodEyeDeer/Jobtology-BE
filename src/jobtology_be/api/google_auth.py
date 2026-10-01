from base64 import urlsafe_b64encode
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated, Protocol
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from jobtology_be.infrastructure.persistence.auth_contracts import (
    ConsumedOAuthLoginAttempt,
    GoogleLogin,
    IssuedSession,
    OAuthLoginAttempt,
)
from jobtology_be.modules.auth.google_oidc import (
    AUTHORIZE_URL,
    GoogleIdentityProvider,
    GoogleProviderError,
)
from jobtology_be.modules.auth.session import hash_session_secret, issue_session_credentials
from jobtology_be.modules.auth.session_cookies import (
    OAUTH_ATTEMPT_COOKIE_NAME,
    clear_oauth_attempt_cookie,
    set_oauth_attempt_cookie,
    set_session_cookie,
)
from jobtology_be.settings import Settings

router = APIRouter(tags=["authentication"])


class GoogleLoginStore(Protocol):
    async def create_oauth_login_attempt(self, attempt: OAuthLoginAttempt) -> None: ...
    async def consume_oauth_login_attempt(
        self, state_hash: bytes, browser_binding_hash: bytes
    ) -> ConsumedOAuthLoginAttempt | None: ...
    async def create_google_session(self, login: GoogleLogin) -> IssuedSession | None: ...


def require_google_settings() -> Settings:
    return Settings()


def require_google_login_store() -> GoogleLoginStore | None:
    return None


def require_google_provider() -> GoogleIdentityProvider | None:
    return None


def _error(status_code: int, *, clear_cookie: bool = False) -> Response:
    response = PlainTextResponse("Authentication unavailable" if status_code == 503 else "Authentication failed",
                                 status_code=status_code, headers={"Cache-Control": "no-store"})
    if clear_cookie:
        clear_oauth_attempt_cookie(response)
    return response


@router.get("/auth/google/login", summary="Start Google login")
async def google_login(
    settings: Annotated[Settings, Depends(require_google_settings)],
    store: Annotated[GoogleLoginStore | None, Depends(require_google_login_store)],
) -> Response:
    config = settings.google_oidc_settings
    if not settings.auth_enabled or not config.is_configured or store is None:
        return _error(503)
    assert config.client_id is not None and config.redirect_uri is not None
    state, nonce, verifier, binding = (token_urlsafe(32) for _ in range(4))
    attempt = OAuthLoginAttempt(
        state_hash=hash_session_secret(state), browser_binding_hash=hash_session_secret(binding),
        nonce=SecretStr(nonce), pkce_verifier=SecretStr(verifier),
    )
    try:
        await store.create_oauth_login_attempt(attempt)
    except SQLAlchemyError:
        return _error(503)
    challenge = urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = urlencode({
        "response_type": "code", "client_id": config.client_id, "redirect_uri": config.redirect_uri,
        "scope": "openid", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    response = RedirectResponse(f"{AUTHORIZE_URL}?{query}", headers={"Cache-Control": "no-store"})
    set_oauth_attempt_cookie(response, binding)
    return response


@router.get("/auth/google/callback", summary="Complete Google login")
async def google_callback(
    request: Request,
    settings: Annotated[Settings, Depends(require_google_settings)],
    store: Annotated[GoogleLoginStore | None, Depends(require_google_login_store)],
    provider: Annotated[GoogleIdentityProvider | None, Depends(require_google_provider)],
) -> Response:
    parameters: dict[str, list[str]] = request.state.oauth_callback_query
    code_values = parameters.get("code", [])
    state_values = parameters.get("state", [])
    error_values = parameters.get("error", [])
    code = code_values[0] if len(code_values) == 1 else None
    state = state_values[0] if len(state_values) == 1 else None
    config = settings.google_oidc_settings
    if not settings.auth_enabled or not config.is_configured or store is None or provider is None:
        return _error(503, clear_cookie=True)
    binding = request.cookies.get(OAUTH_ATTEMPT_COOKIE_NAME)
    if not state or len(state) > 256 or not binding or len(binding) > 256:
        return _error(400, clear_cookie=True)
    try:
        attempt = await store.consume_oauth_login_attempt(
            hash_session_secret(state), hash_session_secret(binding)
        )
    except SQLAlchemyError:
        return _error(503, clear_cookie=True)
    if attempt is None or error_values or not code or len(code) > 4096:
        return _error(400, clear_cookie=True)
    try:
        subject = await provider.subject_for_code(
            code, attempt.pkce_verifier.get_secret_value(), attempt.nonce.get_secret_value()
        )
    except GoogleProviderError:
        return _error(400, clear_cookie=True)
    credentials = issue_session_credentials()
    try:
        issued = await store.create_google_session(
            GoogleLogin(subject, credentials.session_issue(settings.session_lifetime))
        )
    except SQLAlchemyError:
        return _error(503, clear_cookie=True)
    if issued is None:
        return _error(503, clear_cookie=True)
    assert config.frontend_url is not None
    response = RedirectResponse(config.frontend_url, headers={"Cache-Control": "no-store"})
    clear_oauth_attempt_cookie(response)
    set_session_cookie(response, credentials.cookie_value)
    return response

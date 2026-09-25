from dataclasses import dataclass
from hmac import compare_digest
from typing import ClassVar, Final, NoReturn, Protocol
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict

from jobtology_be.modules.auth.session import SessionCredentials, SessionStore
from jobtology_be.modules.auth.session_cookies import SESSION_COOKIE_NAME

_SAFE_HTTP_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class AuthenticatedPrincipal(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    user_id: UUID


class IdentityProvider(Protocol):
    async def current_principal(self) -> AuthenticatedPrincipal: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    principal: AuthenticatedPrincipal
    session_token_hash: bytes
    csrf_token: str


@dataclass(frozen=True, slots=True)
class SessionIdentityProvider:
    session_store: SessionStore
    trusted_origins: frozenset[str]

    async def current_principal(self, request: Request) -> AuthenticatedPrincipal:
        return (await self.current_session(request)).principal

    async def current_session(self, request: Request) -> AuthenticatedSession:
        credentials = _session_credentials(request)
        hashes = credentials.hashes
        session_principal = await self.session_store.resolve_session(hashes.token_hash)
        if session_principal is None or not compare_digest(session_principal.csrf_hash, hashes.csrf_hash):
            _unauthenticated()
        if request.method not in _SAFE_HTTP_METHODS:
            _require_trusted_origin(request, self.trusted_origins)
            _require_csrf_token(request, credentials.csrf_token)
            if not await self.session_store.matches_session_csrf(hashes.token_hash, hashes.csrf_hash):
                _unauthenticated()
        return AuthenticatedSession(
            principal=AuthenticatedPrincipal(user_id=session_principal.user_id),
            session_token_hash=hashes.token_hash,
            csrf_token=credentials.csrf_token,
        )


async def require_authenticated_principal() -> AuthenticatedPrincipal:
    _unauthenticated()


def _session_credentials(request: Request) -> SessionCredentials:
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_value is None:
        _unauthenticated()
    credentials = SessionCredentials.from_cookie_value(cookie_value)
    if credentials is None:
        _unauthenticated()
    return credentials


def _require_trusted_origin(request: Request, trusted_origins: frozenset[str]) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin not in trusted_origins:
        raise HTTPException(status_code=403)


def _require_csrf_token(request: Request, csrf_token: str) -> None:
    provided_token = request.headers.get("X-CSRF-Token")
    if provided_token is None or not compare_digest(provided_token, csrf_token):
        raise HTTPException(status_code=403)


def _unauthenticated() -> NoReturn:
    raise HTTPException(
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )

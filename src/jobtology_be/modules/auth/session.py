from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Final, Protocol

from jobtology_be.infrastructure.persistence.auth_contracts import SessionIssue, SessionPrincipal

_SESSION_SECRET_BYTES: Final = 32
_COOKIE_SECRET_SEPARATOR: Final = "."


@dataclass(frozen=True, slots=True)
class SessionCredentialHashes:
    token_hash: bytes
    csrf_hash: bytes


@dataclass(frozen=True, slots=True)
class SessionCredentials:
    session_token: str
    csrf_token: str

    @property
    def cookie_value(self) -> str:
        return f"{self.session_token}{_COOKIE_SECRET_SEPARATOR}{self.csrf_token}"

    @property
    def hashes(self) -> SessionCredentialHashes:
        return SessionCredentialHashes(
            token_hash=hash_session_secret(self.session_token),
            csrf_hash=hash_session_secret(self.csrf_token),
        )

    def session_issue(self, lifetime: timedelta) -> SessionIssue:
        hashes = self.hashes
        return SessionIssue(
            token_hash=hashes.token_hash,
            csrf_hash=hashes.csrf_hash,
            lifetime=lifetime,
        )

    @classmethod
    def from_cookie_value(cls, value: str) -> "SessionCredentials | None":
        session_token, separator, csrf_token = value.partition(_COOKIE_SECRET_SEPARATOR)
        if (
            separator != _COOKIE_SECRET_SEPARATOR
            or not session_token
            or not csrf_token
            or _COOKIE_SECRET_SEPARATOR in csrf_token
        ):
            return None
        return cls(session_token=session_token, csrf_token=csrf_token)


class SessionStore(Protocol):
    async def resolve_session(self, session_token_hash: bytes) -> SessionPrincipal | None: ...

    async def matches_session_csrf(self, session_token_hash: bytes, csrf_hash: bytes) -> bool: ...

    async def revoke_session(self, session_token_hash: bytes) -> None: ...


def issue_session_credentials() -> SessionCredentials:
    return SessionCredentials(
        session_token=token_urlsafe(_SESSION_SECRET_BYTES),
        csrf_token=token_urlsafe(_SESSION_SECRET_BYTES),
    )


def hash_session_secret(secret: str) -> bytes:
    return sha256(secret.encode("utf-8")).digest()

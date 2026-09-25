from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from pydantic import SecretStr

GOOGLE_ISSUER: Final = "https://accounts.google.com"


@dataclass(frozen=True, slots=True)
class OAuthLoginAttempt:
    state_hash: bytes
    browser_binding_hash: bytes
    nonce: SecretStr
    pkce_verifier: SecretStr


@dataclass(frozen=True, slots=True)
class ConsumedOAuthLoginAttempt:
    nonce: SecretStr
    pkce_verifier: SecretStr


@dataclass(frozen=True, slots=True)
class SessionIssue:
    token_hash: bytes
    csrf_hash: bytes
    lifetime: timedelta


@dataclass(frozen=True, slots=True)
class GoogleLogin:
    google_subject: str
    session: SessionIssue


@dataclass(frozen=True, slots=True)
class IssuedSession:
    user_id: UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    user_id: UUID
    csrf_hash: bytes

import base64
import binascii
import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final, Protocol, override

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql import func

from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import llm_credentials

CHATGPT_OAUTH_PROVIDER: Final = "chatgpt_oauth"
_NONCE_BYTES: Final = 12


class OAuthTokens(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)

    access_token: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    refresh_token: str | None = None
    id_token: str | None = None


class OAuthCredentials(BaseModel):
    """Codex `auth.json` layout; unknown fields are kept so the file round-trips."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)

    tokens: OAuthTokens


@dataclass(frozen=True, slots=True)
class CredentialStoreError(Exception):
    reason: str

    @override
    def __str__(self) -> str:
        return f"credential store error: {self.reason}"


class CredentialStore(Protocol):
    async def load(self) -> OAuthCredentials | None: ...

    async def save(self, credentials: OAuthCredentials) -> None: ...


@dataclass(frozen=True, slots=True)
class FileCredentialStore:
    path: Path

    async def load(self) -> OAuthCredentials | None:
        if not self.path.is_file():
            return None
        try:
            return OAuthCredentials.model_validate_json(self.path.read_text())
        except (OSError, ValidationError) as error:
            raise CredentialStoreError("unreadable credential file") from error

    async def save(self, credentials: OAuthCredentials) -> None:
        self.path.write_text(credentials.model_dump_json(indent=2))


def parse_encryption_key(raw: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError) as error:
        raise CredentialStoreError("encryption key is not base64") from error
    if len(key) != 32:
        raise CredentialStoreError("encryption key must decode to 32 bytes")
    return key


def generate_encryption_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@dataclass(frozen=True, slots=True)
class DatabaseCredentialStore:
    """AES-256-GCM encrypted credentials in the application database.

    The provider name is bound as associated data, so a row cannot be swapped between
    providers, and the key never touches the database.
    """

    database: Database
    key: bytes
    provider: str = CHATGPT_OAUTH_PROVIDER

    async def load(self) -> OAuthCredentials | None:
        async with self.database.sessions() as session:
            ciphertext = await session.scalar(
                select(llm_credentials.c.ciphertext).where(llm_credentials.c.provider == self.provider)
            )
        if ciphertext is None:
            return None
        try:
            plaintext = AESGCM(self.key).decrypt(
                ciphertext[:_NONCE_BYTES], ciphertext[_NONCE_BYTES:], self.provider.encode()
            )
            return OAuthCredentials.model_validate_json(plaintext)
        except (InvalidTag, ValidationError) as error:
            raise CredentialStoreError("stored credentials cannot be decrypted") from error

    async def save(self, credentials: OAuthCredentials) -> None:
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = nonce + AESGCM(self.key).encrypt(
            nonce, credentials.model_dump_json().encode(), self.provider.encode()
        )
        async with self.database.sessions.begin() as session:
            await session.execute(
                insert(llm_credentials)
                .values(provider=self.provider, ciphertext=ciphertext)
                .on_conflict_do_update(
                    index_elements=[llm_credentials.c.provider],
                    set_={"ciphertext": ciphertext, "updated_at": func.now()},
                )
            )

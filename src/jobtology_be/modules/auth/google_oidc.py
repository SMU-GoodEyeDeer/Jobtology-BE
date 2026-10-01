from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Final, Protocol

import anyio
import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError
from pydantic import BaseModel, ConfigDict, ValidationError

from jobtology_be.settings import GoogleOidcSettings

AUTHORIZE_URL: Final = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL: Final = "https://oauth2.googleapis.com/token"
JWKS_URL: Final = "https://www.googleapis.com/oauth2/v3/certs"
_MAX_RESPONSE_BYTES: Final = 65536
_TIMEOUT: Final = httpx.Timeout(10.0, connect=5.0)
_JWT: Final = JsonWebToken(["RS256"])


@dataclass(frozen=True, slots=True)
class GoogleProviderError(Exception):
    pass


class GoogleIdentityProvider(Protocol):
    async def subject_for_code(self, code: str, verifier: str, nonce: str) -> str: ...


class _TokenResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    id_token: str


class _KeySet(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    keys: list[dict[str, str]]


class _IdClaims(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True)
    iss: str
    aud: str | list[str]
    sub: str
    nonce: str
    exp: int
    iat: int
    azp: str | None = None


class GoogleOidcProvider:
    def __init__(
        self, settings: GoogleOidcSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings: GoogleOidcSettings = settings
        self._transport: httpx.AsyncBaseTransport | None = transport

    async def _json(self, client: httpx.AsyncClient, method: str, url: str,
                    *, data: dict[str, str] | None = None) -> bytes:
        try:
            async with client.stream(method, url, data=data) as response:
                if response.status_code != 200 or response.is_redirect:
                    raise GoogleProviderError
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise GoogleProviderError
                return bytes(body)
        except httpx.HTTPError as error:
            raise GoogleProviderError from error

    async def subject_for_code(self, code: str, verifier: str, nonce: str) -> str:
        settings = self._settings
        if not settings.is_configured:
            raise GoogleProviderError
        assert settings.client_id is not None
        assert settings.client_secret is not None
        assert settings.redirect_uri is not None
        try:
            with anyio.fail_after(20):
                async with httpx.AsyncClient(
                    transport=self._transport, timeout=_TIMEOUT, follow_redirects=False,
                    trust_env=False,
                ) as client:
                    token_body = await self._json(client, "POST", TOKEN_URL, data={
                        "code": code,
                        "client_id": settings.client_id,
                        "client_secret": settings.client_secret.get_secret_value(),
                        "redirect_uri": settings.redirect_uri,
                        "code_verifier": verifier,
                        "grant_type": "authorization_code",
                    })
                    token = _TokenResponse.model_validate_json(token_body)
                    keys = _KeySet.model_validate_json(await self._json(client, "GET", JWKS_URL))
                    claims = _JWT.decode(
                        token.id_token, JsonWebKey.import_key_set({"keys": keys.keys}),
                        claims_options={
                            name: {"essential": True}
                            for name in ("iss", "aud", "sub", "nonce", "exp", "iat")
                        },
                    )
                    claims.validate(leeway=0)
                    identity = _IdClaims.model_validate(dict(claims))
        except (JoseError, ValidationError, ValueError, TypeError, KeyError, TimeoutError) as error:
            raise GoogleProviderError from error
        now = int(datetime.now(UTC).timestamp())
        audiences = [identity.aud] if isinstance(identity.aud, str) else identity.aud
        if (
            identity.iss not in ("https://accounts.google.com", "accounts.google.com")
            or settings.client_id not in audiences
            or len(audiences) != len(set(audiences))
            or not identity.sub.strip()
            or identity.nonce != nonce
            or identity.iat > now
            or identity.exp <= now
            or (len(audiences) > 1 and identity.azp != settings.client_id)
            or (identity.azp is not None and identity.azp != settings.client_id)
        ):
            raise GoogleProviderError
        return identity.sub

"""Server-only validation of the existing Turnstile widget's single-use tokens."""

from asyncio import timeout
from collections.abc import Collection
from typing import Protocol

import httpx
from pydantic import SecretStr

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
GOOGLE_LOGIN_ACTION = "google_login"


class TurnstileVerifier(Protocol):
    async def verify(self, token: str) -> bool: ...


class CloudflareTurnstileVerifier:
    def __init__(
        self, secret: SecretStr, hostnames: Collection[str], *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._secret = secret
        self._hostnames = frozenset(hostnames)
        self._transport = transport

    async def verify(self, token: str) -> bool:
        if not token.strip() or len(token) > 2048 or not self._hostnames:
            return False
        try:
            async with timeout(10), httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
                response = await client.post(SITEVERIFY_URL, data={
                    "secret": self._secret.get_secret_value(), "response": token,
                })
                response.raise_for_status()
                result = response.json()
        except (TimeoutError, httpx.HTTPError, ValueError):
            return False
        return (
            isinstance(result, dict)
            and result.get("success") is True
            and result.get("action") == GOOGLE_LOGIN_ACTION
            and isinstance(result.get("hostname"), str)
            and result["hostname"] in self._hostnames
        )

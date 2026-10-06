import base64
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol, override

import anyio
import httpx
from pydantic import BaseModel, ValidationError

from jobtology_be.infrastructure.persistence.contracts import JsonValue
from jobtology_be.llm.credentials import CredentialStore, CredentialStoreError, OAuthCredentials

type ChatRole = Literal["user", "assistant"]

OPENAI_RESPONSES_URL: Final = "https://api.openai.com/v1/responses"
CHATGPT_CODEX_RESPONSES_URL: Final = "https://chatgpt.com/backend-api/codex/responses"
CHATGPT_OAUTH_TOKEN_URL: Final = "https://auth.openai.com/oauth/token"
CHATGPT_OAUTH_CLIENT_ID: Final = "app_EMoamEEZ73f0CkXaXp7hrann"
_REFRESH_MARGIN_SECONDS: Final = 300


@dataclass(frozen=True, slots=True)
class LlmMessage:
    role: ChatRole
    text: str


@dataclass(frozen=True, slots=True)
class LlmUnavailableError(Exception):
    reason: str

    @override
    def __str__(self) -> str:
        return f"LLM unavailable: {self.reason}"


class LlmClient(Protocol):
    async def complete_json(
        self,
        *,
        instructions: str,
        messages: Sequence[LlmMessage],
        schema_name: str,
        schema: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]: ...


def _input(messages: Sequence[LlmMessage]) -> list[dict[str, JsonValue]]:
    return [
        {
            "role": message.role,
            "content": [
                {
                    "type": "input_text" if message.role == "user" else "output_text",
                    "text": message.text,
                }
            ],
        }
        for message in messages
    ]


def _body(
    model: str,
    instructions: str,
    messages: Sequence[LlmMessage],
    schema_name: str,
    schema: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return {
        "model": model,
        "instructions": instructions,
        "input": _input(messages),
        "store": False,
        "text": {
            "format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": dict(schema)}
        },
    }


def _parse(text: str) -> dict[str, JsonValue]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise LlmUnavailableError("model returned invalid JSON") from error
    if not isinstance(parsed, dict):
        raise LlmUnavailableError("model returned a non-object")
    return parsed


@dataclass(slots=True)
class OpenAIApiKeyClient:
    api_key: str
    model: str
    timeout_seconds: float = 60.0

    async def complete_json(
        self,
        *,
        instructions: str,
        messages: Sequence[LlmMessage],
        schema_name: str,
        schema: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    OPENAI_RESPONSES_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=_body(self.model, instructions, messages, schema_name, schema),
                )
        except httpx.HTTPError as error:
            raise LlmUnavailableError("request failed") from error
        if response.status_code != 200:
            raise LlmUnavailableError(f"status {response.status_code}")
        payload = response.json()
        text = "".join(
            part.get("text", "")
            for item in payload.get("output", [])
            if item.get("type") == "message"
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        )
        return _parse(text)


@dataclass(slots=True)
class ChatGptOAuthClient:
    """ChatGPT-account OAuth (Codex login) against the Codex Responses backend.

    Credentials are loaded from the store on each call so an operator can replace them
    without a restart. Refreshing rotates the refresh token; enable it only for a login
    dedicated to this server, otherwise the same login on another device is signed out.
    """

    store: CredentialStore
    model: str
    refresh_enabled: bool = False
    timeout_seconds: float = 90.0
    http_transport: httpx.AsyncBaseTransport | None = None
    _lock: anyio.Lock = field(default_factory=anyio.Lock)

    async def complete_json(
        self,
        *,
        instructions: str,
        messages: Sequence[LlmMessage],
        schema_name: str,
        schema: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        access_token, account_id = await self.credentials()
        body = {**_body(self.model, instructions, messages, schema_name, schema), "stream": True}
        text = ""
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout_seconds) as client,
                client.stream(
                    "POST",
                    CHATGPT_CODEX_RESPONSES_URL,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "chatgpt-account-id": account_id,
                        "Accept": "text/event-stream",
                        "OpenAI-Beta": "responses=experimental",
                        "originator": "codex_cli_rs",
                    },
                    json=body,
                ) as response,
            ):
                if response.status_code != 200:
                    raise LlmUnavailableError(f"status {response.status_code}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:])
                    if event.get("type") == "response.output_text.delta":
                        text += event.get("delta", "")
                    elif event.get("type") in {"response.failed", "error"}:
                        raise LlmUnavailableError("model response failed")
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            raise LlmUnavailableError("request failed") from error
        return _parse(text)

    async def credentials(self) -> tuple[str, str]:
        async with self._lock:
            try:
                document = await self.store.load()
            except CredentialStoreError as error:
                raise LlmUnavailableError("OAuth credentials unreadable") from error
            if document is None:
                raise LlmUnavailableError("OAuth credentials not configured")
            if self.refresh_enabled and _expires_soon(document.tokens.access_token):
                document = await self._refresh(document)
            if _expires_soon(document.tokens.access_token, margin=0):
                raise LlmUnavailableError("OAuth access token expired")
            return document.tokens.access_token, document.tokens.account_id

    async def _refresh(self, document: OAuthCredentials) -> OAuthCredentials:
        if not document.tokens.refresh_token:
            raise LlmUnavailableError("OAuth refresh token missing")
        try:
            async with httpx.AsyncClient(timeout=30, transport=self.http_transport) as client:
                response = await client.post(
                    CHATGPT_OAUTH_TOKEN_URL,
                    json={
                        "client_id": CHATGPT_OAUTH_CLIENT_ID,
                        "grant_type": "refresh_token",
                        "refresh_token": document.tokens.refresh_token,
                        "scope": "openid profile email",
                    },
                )
        except httpx.HTTPError as error:
            raise LlmUnavailableError("OAuth refresh failed") from error
        if response.status_code != 200:
            raise LlmUnavailableError(f"OAuth refresh status {response.status_code}")
        try:
            refreshed = _RefreshResponse.model_validate_json(response.content)
        except ValidationError as error:
            raise LlmUnavailableError("OAuth refresh response invalid") from error
        updated = document.model_copy(
            update={
                "tokens": document.tokens.model_copy(
                    update={key: value for key, value in refreshed.model_dump().items() if value is not None}
                )
            }
        )
        await self.store.save(updated)
        return updated


class _RefreshResponse(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    id_token: str | None = None


def _expires_soon(access_token: str, margin: int = _REFRESH_MARGIN_SECONDS) -> bool:
    try:
        payload = access_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"]) - time.time() <= margin
    except (IndexError, ValueError, KeyError, json.JSONDecodeError):
        return True

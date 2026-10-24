"""Bounded provider SSE decoding. Provider reasoning never becomes a UI event."""
import json
from collections.abc import AsyncIterator, Mapping

import httpx

from jobtology_be.infrastructure.persistence.contracts import JsonValue


class ProviderStreamError(Exception):
    pass


async def response_events(url: str, headers: Mapping[str, str], body: dict,
                          timeout: float, transport: httpx.AsyncBaseTransport | None = None
                          ) -> AsyncIterator[dict[str, JsonValue]]:
    completed = False
    data: list[str] = []
    size = 0
    async with (
        httpx.AsyncClient(timeout=timeout, transport=transport) as client,
        client.stream("POST", url, headers=headers, json=body) as response,
    ):
        if response.status_code != 200:
            raise ProviderStreamError(f"status {response.status_code}")
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
                size += len(line)
                if size > 700_000:
                    raise ProviderStreamError("provider event too large")
                continue
            if line or not data:
                continue
            payload = "\n".join(data)
            data, size = [], 0
            if payload == "[DONE]":
                continue
            event = json.loads(payload)
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ProviderStreamError("invalid provider event")
            kind = event["type"]
            if kind in {"response.failed", "response.incomplete", "error"}:
                raise ProviderStreamError("provider response failed")
            if completed:
                raise ProviderStreamError("event after completion")
            if kind == "response.completed":
                completed = True
            if kind in {"response.output_text.delta", "response.output_item.done", "response.completed"}:
                yield event
    if not completed:
        raise ProviderStreamError("provider stream interrupted")

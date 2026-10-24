import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import httpx
import pytest

from jobtology_be.llm.client import ChatGptOAuthClient, LlmUnavailableError, OpenAIApiKeyClient
from jobtology_be.llm.credentials import OAuthCredentials


class Bytes(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body
        self.closed = False

    async def __aiter__(self):
        chunk = 7 if len(self.body) < 10_000 else len(self.body)
        for i in range(0, len(self.body), chunk):
            yield self.body[i:i + chunk]

    async def aclose(self):
        self.closed = True


def sse(*events):
    return (": heartbeat\r\n\r\n" + "".join("data: " + json.dumps(e, ensure_ascii=False) + "\r\n\r\n" for e in events)).encode()


def client(kind, handler):
    transport = httpx.MockTransport(handler)
    if kind == "api":
        return OpenAIApiKeyClient("synthetic-key", "configured-model", http_transport=transport)
    claims = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 3600}).encode()).decode().rstrip("=")
    credentials = OAuthCredentials.model_validate({"tokens": {"access_token": f"h.{claims}.s", "account_id": "synthetic-account"}})
    return ChatGptOAuthClient(SimpleNamespace(load=AsyncMock(return_value=credentials)), "configured-model", http_transport=transport)


async def collect(model):
    return [event async for event in model.stream_response(instructions="test", input=[], tools=[], tool_choice="none")]


@pytest.mark.parametrize("kind", ["api", "oauth"])
def test_provider_stream_preserves_unicode_and_request_configuration(kind):
    body = Bytes(sse({"type": "response.output_text.delta", "delta": "한국어 😀"},
                     {"type": "response.completed", "response": {"output": []}}))
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=body)
    events = anyio.run(collect, client(kind, handler))
    assert events[0]["delta"] == "한국어 😀"
    assert events[-1]["type"] == "response.completed" and body.closed
    assert requests[0]["stream"] is True and requests[0]["store"] is False
    assert requests[0]["model"] == "configured-model" and requests[0]["tool_choice"] == "none"


@pytest.mark.parametrize("kind", ["api", "oauth"])
def test_closing_a_suspended_provider_stream_closes_http_immediately(kind):
    body = Bytes(sse({"type": "response.output_text.delta", "delta": "partial"},
                     {"type": "response.completed", "response": {"output": []}}))
    model = client(kind, lambda request: httpx.Response(200, stream=body))
    async def run():
        events = model.stream_response(instructions="test", input=[], tools=[])
        assert (await anext(events))["delta"] == "partial"
        await events.aclose()
        assert body.closed
    anyio.run(run)


@pytest.mark.parametrize("terminal", [None, "response.failed", "response.incomplete", "error"])
def test_provider_stream_rejects_interruption_and_failed_terminal(terminal):
    events = [{"type": "response.output_text.delta", "delta": "partial"}]
    if terminal:
        events.append({"type": terminal})
    body = Bytes(sse(*events))
    with pytest.raises(LlmUnavailableError):
        anyio.run(collect, client("api", lambda request: httpx.Response(200, stream=body)))
    assert body.closed


def test_provider_stream_accepts_multiline_sse_and_rejects_oversized_events():
    body = Bytes(b'data: {"type":"response.completed",\ndata: "response":{"output":[]}}\n\n')
    assert anyio.run(collect, client("api", lambda request: httpx.Response(200, stream=body)))[0]["type"] == "response.completed"
    oversized = Bytes(b"data: " + b"x" * 700_001 + b"\n\n")
    with pytest.raises(LlmUnavailableError):
        anyio.run(collect, client("api", lambda request: httpx.Response(200, stream=oversized)))
    assert oversized.closed

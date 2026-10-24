import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from test_chat_workspace import Model, personal_fixture

from jobtology_be.api.chat import require_chat_rate_limiter
from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.chat.workspace import ChatWorkspaceService
from jobtology_be.chat.workspace_models import EvidenceGraph, GraphNode, WorkspaceEvidence
from jobtology_be.infrastructure.persistence.rate_limit import RateLimitDecision
from jobtology_be.llm.client import LlmMessage
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

USER, GOAL = uuid4(), uuid4()
INITIAL = WorkspaceEvidence(goal_id=GOAL, graph=EvidenceGraph(nodes=(GraphNode(id="personal", kind="capability", label="내 경험"),)))


def call(key, name, arguments):
    return {"type": "function_call", "call_id": key, "name": name, "arguments": json.dumps(arguments)}


class NativeModel:
    def __init__(self, rounds, reply='한글 😀 "인용"\n다음 줄'):
        self.rounds = list(rounds)
        self.requests = []
        self.answer = {**Model().answer, "reply": reply, "source_ids": ["personal"], "node_ids": ["personal"]}

    async def stream_response(self, **kwargs):
        self.requests.append({**kwargs, "input": list(kwargs["input"])})
        if kwargs.get("schema_name"):
            text = json.dumps(self.answer)
            for i in range(0, len(text), 3):
                yield {"type": "response.output_text.delta", "delta": text[i:i + 3]}
            yield {"type": "response.completed", "response": {"output": []}}
        else:
            yield {"type": "response.completed", "response": {"output": self.rounds.pop(0) if self.rounds else []}}


async def collect(service):
    return [event async for event in service.events(INITIAL, (LlmMessage("user", "내 경험과 SQL을 연결해줘"),), user_id=USER)]


def test_native_reads_are_iterative_and_answer_text_arrives_before_validated_graph_actions():
    model = NativeModel([[call("first", "search_catalog", {"query": "SQL"})],
                         [call("second", "expand_connections", {"node_id": "sql"})], []])
    graph = EvidenceGraph(nodes=(GraphNode(id="sql", kind="technology", label="SQL"),))
    catalog = SimpleNamespace(search=AsyncMock(return_value=graph), expand=AsyncMock(return_value=graph))
    events = anyio.run(collect, ChatWorkspaceService(None, model, catalog))
    catalog.expand.assert_awaited_once_with("sql")
    assert any(item.get("type") == "function_call_output" and item["call_id"] == "first" for item in model.requests[1]["input"])
    deltas = [e["delta"] for e in events if e["type"] == "answer_delta"]
    assert ''.join(deltas) == model.answer["reply"]
    assert next(i for i, e in enumerate(events) if e["type"] == "answer_delta") < next(i for i, e in enumerate(events) if e["type"] == "answer")
    assert len([e for e in events if e["type"] == "answer"]) == 1
    assert [(e["name"], e["status"]) for e in events if e["type"] == "tool"] == [
        ("search_catalog", "started"), ("search_catalog", "completed"), ("expand_connections", "started"), ("expand_connections", "completed")]
    assert events[-1] == {"type": "done"}


def test_personal_tool_scope_is_server_owned_and_injected_user_id_is_rejected():
    model = NativeModel([[call("invalid", "read_personal_records", {"query": "경험", "user_id": str(uuid4())}),
                          call("valid", "read_personal_records", {"query": "경험"})], []])
    personal = SimpleNamespace(retrieve=AsyncMock(return_value=INITIAL))
    events = anyio.run(collect, ChatWorkspaceService(personal, model))
    personal.retrieve.assert_awaited_once_with(USER, GOAL, "경험")
    assert any(e["type"] == "tool" and e["call_id"] == "invalid" and e["status"] == "failed" for e in events)
    assert events[-1] == {"type": "done"}


def test_focused_and_occupation_context_remain_visible_beyond_the_initial_source_sample():
    nodes = (*INITIAL.graph.nodes, *(GraphNode(id=f"skill:{i}", kind="technology", label="SQL") for i in range(40)),
             GraphNode(id="postings:20010202", kind="posting_evidence", label="역사 공고", occupation_code="20010202"),
             GraphNode(id="focus", kind="technology", label="선택한 역량", detail="집중해서 볼 근거"))
    model = NativeModel([])
    model.answer.update(source_ids=["focus"], node_ids=["focus"])
    async def run():
        return [e async for e in ChatWorkspaceService(None, model).events(
            INITIAL.model_copy(update={"graph": EvidenceGraph(nodes=nodes)}), (LlmMessage("user", "SQL 학습"),), ["focus"], user_id=USER)]
    events = anyio.run(run)
    prompt = json.loads(model.requests[0]["input"][-1]["content"][0]["text"])
    assert prompt["focused_evidence"]["sources"][0]["id"] == "focus"
    assert prompt["occupation_context"][0]["occupation_code"] == "20010202"
    answer = next(e["data"] for e in events if e["type"] == "answer")
    assert answer["sources"][0]["id"] == "focus"


def test_call_budget_prevents_excess_native_reads_and_is_disclosed():
    model = NativeModel([[call("one", "search_catalog", {"query": "SQL"}), call("two", "search_catalog", {"query": "Python"})]])
    catalog = SimpleNamespace(search=AsyncMock(return_value=EvidenceGraph()))
    events = anyio.run(collect, ChatWorkspaceService(None, model, catalog, max_tool_calls=1))
    assert catalog.search.await_count == 1
    answer = next(e["data"] for e in events if e["type"] == "answer")
    assert "TOOL_BUDGET_REACHED" in answer["warnings"]


@pytest.mark.parametrize("parallel", [False, True])
def test_parallel_read_setting_bounds_actual_execution(parallel):
    active = maximum = 0
    async def search(query):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(.01)
        active -= 1
        return EvidenceGraph()
    model = NativeModel([[call("one", "search_catalog", {"query": "SQL"}), call("two", "search_catalog", {"query": "Python"})], []])
    events = anyio.run(collect, ChatWorkspaceService(None, model, SimpleNamespace(search=search), parallel_tool_calls=parallel))
    assert maximum == (2 if parallel else 1)
    assert model.requests[0]["parallel_tool_calls"] is parallel and events[-1] == {"type": "done"}


def test_cancelling_an_inflight_native_read_cancels_children_and_releases_capacity():
    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def search(query):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        model = NativeModel([[call("one", "search_catalog", {"query": "SQL"})]])
        service = ChatWorkspaceService(None, model, SimpleNamespace(search=search))
        task = asyncio.create_task(collect(service))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set() and service._slots._value == 4
    anyio.run(run)


def test_closing_suspended_answer_closes_model_and_releases_capacity_immediately():
    async def run():
        closed = asyncio.Event()
        class ObservedModel(NativeModel):
            async def stream_response(self, **kwargs):
                try:
                    async for event in super().stream_response(**kwargs):
                        yield event
                finally:
                    if kwargs.get("schema_name"):
                        closed.set()
        service = ChatWorkspaceService(None, ObservedModel([]))
        events = service.events(INITIAL, (LlmMessage("user", "내 경험"),), user_id=USER)
        async for event in events:
            if event["type"] == "answer_delta":
                break
        await events.aclose()
        assert closed.is_set() and service._slots._value == 4
    anyio.run(run)


def test_disabled_tools_skip_retrieval_and_keep_incremental_answering():
    model = NativeModel([])
    events = anyio.run(collect, ChatWorkspaceService(None, model, tool_choice="none"))
    assert len(model.requests) == 1 and model.requests[0]["tool_choice"] == "none"
    assert any(e["type"] == "answer_delta" for e in events)


def test_http_sse_negotiation_exposes_tool_and_text_events_without_breaking_ndjson_clients():
    personal, _, _ = personal_fixture()
    class Principal:
        async def current_principal(self):
            return AuthenticatedPrincipal(user_id=USER)
    principal = Principal()
    app = create_app(Settings(enable_fixtures=False), dependencies=ApiDependencies(
        identity_provider=principal, chat_workspace=ChatWorkspaceService(personal, NativeModel([])),
    ))
    app.dependency_overrides[require_chat_rate_limiter] = lambda: SimpleNamespace(consume=AsyncMock(return_value=RateLimitDecision(True, 0)))
    with TestClient(app) as client:
        response = client.post('/api/v1/chat/stream', headers={'Accept':'text/event-stream'}, json={'messages':[{'role':'user','text':'내 정보'}]})
    assert response.status_code == 200 and response.headers['content-type'].startswith('text/event-stream')
    assert 'event: answer_delta\ndata: ' in response.text and 'event: done\n' in response.text


def test_sse_heartbeats_do_not_cancel_a_slow_provider_read(monkeypatch):
    import jobtology_be.api.chat as chat_api
    monkeypatch.setattr(chat_api, "CHAT_HEARTBEAT_SECONDS", .001)
    personal, _, _ = personal_fixture()
    class SlowModel(NativeModel):
        async def stream_response(self, **kwargs):
            await asyncio.sleep(.02)
            async for event in super().stream_response(**kwargs):
                yield event
    class Principal:
        async def current_principal(self):
            return AuthenticatedPrincipal(user_id=USER)
    app = create_app(Settings(enable_fixtures=False), dependencies=ApiDependencies(
        identity_provider=Principal(), chat_workspace=ChatWorkspaceService(personal, SlowModel([])),
    ))
    app.dependency_overrides[require_chat_rate_limiter] = lambda: SimpleNamespace(consume=AsyncMock(return_value=RateLimitDecision(True, 0)))
    with TestClient(app) as client:
        response = client.post('/api/v1/chat/stream', headers={'Accept':'text/event-stream'}, json={'messages':[{'role':'user','text':'내 정보'}]})
    assert ': keep-alive\n\n' in response.text
    assert 'event: answer\n' in response.text and 'event: done\n' in response.text


def test_repeated_call_ids_are_rejected_before_executing_the_batch():
    model = NativeModel([[call("same", "search_catalog", {"query": "SQL"}), call("same", "search_catalog", {"query": "Python"})]])
    catalog = SimpleNamespace(search=AsyncMock())
    events = anyio.run(collect, ChatWorkspaceService(None, model, catalog))
    catalog.search.assert_not_awaited()
    assert not any(e["type"] == "tool" for e in events)
    assert "SEARCH_PLANNER_UNAVAILABLE" in next(e["data"]["warnings"] for e in events if e["type"] == "answer")

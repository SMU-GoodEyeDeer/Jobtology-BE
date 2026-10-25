import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest
from asyncpg import PostgresError
from sqlalchemy.exc import DBAPIError
from test_chat_native_tools import INITIAL, USER, NativeModel
from test_ncs_onet_crosswalk import response

import jobtology_be.infrastructure.persistence.live_source_feed as live
from jobtology_be.chat.tools import ReadTools
from jobtology_be.chat.workspace import ChatWorkspaceService
from jobtology_be.chat.workspace_models import EvidenceGraph, GraphNode
from jobtology_be.llm.client import LlmMessage


def test_concurrent_identical_crosswalk_reads_share_one_database_query(monkeypatch):
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()
        data = response().model_copy(update={"limit": 6})
        async def read(*args):
            started.set()
            await finish.wait()
            return data
        query = AsyncMock(side_effect=read)
        monkeypatch.setattr(live.PostgresLiveSourceFeed, "_read", query)
        feed = live.PostgresLiveSourceFeed(None)
        first = asyncio.create_task(feed.crosswalk("ncs:occupation:20010202", 6, 0))
        await started.wait()
        others = [asyncio.create_task(feed.crosswalk("ncs:occupation:20010202", 6, 0)) for _ in range(3)]
        finish.set()
        values = await asyncio.gather(first, *others)
        assert values == [data] * 4 and query.await_count == 1
    anyio.run(run)


def test_crosswalk_reads_share_overflow_keys_when_more_than_64_keys_are_inflight(monkeypatch):
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()
        first_key = "ncs:occupation:20010000"
        overflow_key = "ncs:occupation:20010064"
        calls = []
        async def read(operation, params, model):
            calls.append(params["node_id"])
            if params["node_id"] == first_key:
                started.set()
                await finish.wait()
            return response()
        monkeypatch.setattr(live.PostgresLiveSourceFeed, "_read", AsyncMock(side_effect=read))
        feed = live.PostgresLiveSourceFeed(None)
        owner = asyncio.create_task(feed.crosswalk(first_key, 6, 0))
        await started.wait()
        pending = [asyncio.create_task(feed.crosswalk(f"ncs:occupation:{20010000 + i}", 6, 0)) for i in range(1, 65)]
        await asyncio.sleep(0)
        duplicate = asyncio.create_task(feed.crosswalk(overflow_key, 6, 0))
        await asyncio.sleep(0)
        finish.set()
        await asyncio.gather(owner, *pending, duplicate)
        assert calls.count(overflow_key) == 1 and len(calls) == 65
        assert len(feed._crosswalk_reads) <= 64
        assert not feed._crosswalk_inflight
    anyio.run(run)


def test_cancelling_crosswalk_read_cleans_up_inflight_state_and_allows_retry(monkeypatch):
    async def run():
        started = asyncio.Event()
        async def read(*args):
            started.set()
            await asyncio.Event().wait()
        query = AsyncMock(side_effect=read)
        monkeypatch.setattr(live.PostgresLiveSourceFeed, "_read", query)
        feed = live.PostgresLiveSourceFeed(None)
        owner = asyncio.create_task(feed.crosswalk("ncs:occupation:20010202", 6, 0))
        await started.wait()
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert not feed._crosswalk_inflight
        query.side_effect, query.return_value = None, response()
        await feed.crosswalk("ncs:occupation:20010202", 6, 0)
        assert query.await_count == 2 and not feed._crosswalk_inflight
    anyio.run(run)


def test_crosswalk_cache_expires_and_separates_pages(monkeypatch):
    async def run():
        now = [100.0]
        monkeypatch.setattr(live, "time", SimpleNamespace(monotonic=lambda: now[0]))
        query = AsyncMock(return_value=response())
        monkeypatch.setattr(live.PostgresLiveSourceFeed, "_read", query)
        feed = live.PostgresLiveSourceFeed(None)
        await feed.crosswalk("ncs:occupation:20010202", 6, 0)
        await feed.crosswalk("ncs:occupation:20010202", 6, 0)
        assert query.await_count == 1
        await feed.crosswalk("ncs:occupation:20010202", 6, 6)
        await feed.crosswalk("ncs:occupation:20010202", 100, 0)
        assert query.await_count == 3
        now[0] += 31
        await feed.crosswalk("ncs:occupation:20010202", 6, 0)
        assert query.await_count == 4
    anyio.run(run)


def test_distinct_crosswalk_misses_do_not_compete_for_database_cpu(monkeypatch):
    async def run():
        active = maximum = 0
        async def read(*args):
            nonlocal active, maximum
            active += 1
            maximum = max(active, maximum)
            await asyncio.sleep(.001)
            active -= 1
            return response()
        monkeypatch.setattr(live.PostgresLiveSourceFeed, "_read", AsyncMock(side_effect=read))
        feed = live.PostgresLiveSourceFeed(None)
        await asyncio.gather(*(feed.crosswalk(f"ncs:occupation:{20010200 + i}", 6, 0) for i in range(4)))
        assert maximum == 1
    anyio.run(run)


def test_crosswalk_failure_is_not_cached_and_active_reader_survives_cache_eviction(monkeypatch):
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()
        attempts = 0
        async def read(operation, params, model):
            nonlocal attempts
            if params["node_id"] == "ncs:occupation:20010202":
                attempts += 1
                if attempts == 1:
                    raise live.LiveSourceReadError(503)
                started.set()
                await finish.wait()
            return response()
        query = AsyncMock(side_effect=read)
        monkeypatch.setattr(live.PostgresLiveSourceFeed, "_read", query)
        feed = live.PostgresLiveSourceFeed(None)
        with pytest.raises(live.LiveSourceReadError):
            await feed.crosswalk("ncs:occupation:20010202", 6, 0)
        assert not feed._crosswalk_inflight
        owner = asyncio.create_task(feed.crosswalk("ncs:occupation:20010202", 6, 0))
        await started.wait()
        waiter = asyncio.create_task(feed.crosswalk("ncs:occupation:20010202", 6, 0))
        pending = [asyncio.create_task(feed.crosswalk(f"ncs:occupation:{30000000 + index}", 6, 0)) for index in range(70)]
        # Allow requests to enter the bounded cache while the first read owns the slot.
        await asyncio.sleep(0)
        assert len(feed._crosswalk_reads) <= 64
        finish.set()
        await asyncio.gather(owner, waiter, *pending)
        assert attempts == 2
    anyio.run(run)


@pytest.mark.parametrize("database_error", [False, True])
def test_failed_reader_logs_safe_diagnostics_without_sql_parameters_or_credentials(caplog, database_error):
    error = TimeoutError("private://credential and private payload")
    if database_error:
        cause = PostgresError.new({"M": "private payload", "C": "57014"})
        wrapper = RuntimeError("private://credential")
        wrapper.__cause__ = cause
        error = DBAPIError("private SQL", {"password": "secret"}, wrapper)
    def connect():
        raise error
    feed = live.PostgresLiveSourceFeed(SimpleNamespace(connect=connect))
    async def run():
        with pytest.raises(live.LiveSourceReadError) as found:
            await feed.crosswalk("private parameter", 6, 0)
        assert found.value.status_code == 503
    anyio.run(run)
    assert "operation=crosswalk" in caplog.text and "elapsed_ms=" in caplog.text
    assert "private" not in caplog.text and "secret" not in caplog.text
    if database_error:
        assert "sqlstate=57014" in caplog.text


def test_requirement_catalog_reference_reaches_llm_and_read_tools_without_rewriting_assessment():
    requirement = GraphNode(id="requirement:BACKEND_DEVELOPER:2001020406", kind="ncs_competency", label="저장된 평가",
        ncs_base_code="2001020406", basis="REVIEWED_REQUIREMENT", source_pin="old-analysis-release", detail="UNMET")
    catalog_node = GraphNode(id="ncs:unit:2001020406", kind="ncs_competency", label="현재 카탈로그", source_pin="current-catalog")
    catalog = SimpleNamespace(expand=AsyncMock(return_value=EvidenceGraph(nodes=(catalog_node,))))
    model = NativeModel([])
    context = INITIAL.model_copy(update={"graph": EvidenceGraph(nodes=(*INITIAL.graph.nodes, requirement))})
    async def run():
        events = [event async for event in ChatWorkspaceService(None, model, catalog).events(
            context, (LlmMessage("user", "이 역량의 연결 자료를 설명해줘"),), [requirement.id], user_id=USER)]
        assert events[-1] == {"type": "done"}
        found = await ReadTools(None, catalog, USER, context.goal_id).execute("expand_connections",
            '{"node_id":"' + requirement.id + '"}', context.graph)
        assert any(link.source == requirement.id and link.target == catalog_node.id and link.kind == "CATALOG_REFERENCE" for link in found.edges)
        assert next(node for node in found.nodes if node.id == requirement.id) == requirement
    anyio.run(run)
    assert all(call.args == (catalog_node.id,) for call in catalog.expand.await_args_list)
    import json
    prompt = json.loads(model.requests[-1]["input"][-1]["content"][0]["text"])
    assert any(link["kind"] == "CATALOG_REFERENCE" for link in prompt["relationships"])
    assert next(source for source in prompt["sources"] if source["id"] == requirement.id)["source_pin"] == "old-analysis-release"

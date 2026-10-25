import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest
from test_chat_native_tools import INITIAL, USER, NativeModel

from jobtology_be.chat.evidence import focus_neighborhood
from jobtology_be.chat.tools import tool_output
from jobtology_be.chat.workspace import ChatWorkspaceService
from jobtology_be.chat.workspace_models import EvidenceGraph, GraphEdge, GraphNode, edge
from jobtology_be.llm.client import LlmMessage

POSTING = GraphNode(id="posting:jobkorea:one", kind="posting", label="백엔드 채용", detail="모집 중인 공고 메타데이터")
NCS = GraphNode(id="ncs:occupation:20010204", kind="ncs_occupation", label="DB엔지니어링", occupation_code="20010204")
ONET = GraphNode(id="urn:jobtology:onet:occupation:15-1252.00", kind="onet_occupation", label="Software Developers", occupation_code="15-1252.00")
UNIT = GraphNode(id="ncs:unit:2001020413", kind="ncs_competency", label="데이터베이스 구현", source_pin="ncs-current")
SKILL = GraphNode(id="urn:jobtology:onet-detail:database", kind="onet_skill", label="Database", source_pin="onet-current")
MAPPING = GraphEdge(id="crosswalk:database", source=UNIT.id, target=SKILL.id, kind="CROSSWALK_RELATED", basis="PROPOSED",
    mapping_version="bilingual-technical-cues-v1", source_quote="데이터베이스", target_quote="database",
    source_run_id="ncs-current", catalog_pin="onet-current", review_status="UNREVIEWED")


def fixtures(stale=False):
    links = tuple(GraphEdge(id=f"classification:{node.id}", source=POSTING.id, target=node.id,
        kind="INFERRED_POSTING_OCCUPATION", basis="METADATA_INFERRED", source_freshness="CURRENT",
        catalog_freshness="STALE" if stale else "CURRENT", source_run_id="posting-run", catalog_pin="classification-pin") for node in (NCS, ONET))
    classifications = EvidenceGraph(nodes=(POSTING, NCS, ONET), edges=links)
    async def expand(identity):
        if identity == NCS.id:
            return EvidenceGraph(nodes=(NCS, UNIT, SKILL), edges=(edge(UNIT.id, NCS.id, "CLASSIFIED_AS", "REFERENCE"), MAPPING))
        assert identity == ONET.id
        return EvidenceGraph(nodes=(ONET, SKILL), edges=(edge(ONET.id, SKILL.id, "ONET_ESSENTIAL_SKILL", "REFERENCE"),))
    catalog = SimpleNamespace(node=AsyncMock(return_value=classifications), expand=AsyncMock(side_effect=expand))
    return catalog, classifications


def prompt(request):
    return json.loads(request["input"][-1]["content"][0]["text"])


def test_posting_question_is_grounded_even_when_model_requests_no_tools_and_account_evidence_is_large():
    catalog, _ = fixtures()
    noise = tuple(GraphNode(id=f"noise:{i}", kind="technology", label="SQL") for i in range(90))
    noisy = EvidenceGraph(nodes=(*INITIAL.graph.nodes, *noise), edges=tuple(
        GraphEdge(id=f"noise-edge:{i}", source=noise[0].id, target=noise[(i % 89) + 1].id, kind="RELATED", basis="REFERENCE") for i in range(200)))
    model = NativeModel([])
    model.answer.update(source_ids=[UNIT.id, SKILL.id], node_ids=[UNIT.id, SKILL.id], edge_ids=[MAPPING.id])
    async def run():
        return [event async for event in ChatWorkspaceService(None, model, catalog).events(
            INITIAL.model_copy(update={"graph": noisy}), (LlmMessage("user", "이 공고와 SQL 역량의 연결을 설명해줘"),), [POSTING.id], user_id=USER)]
    events = anyio.run(run)
    assert {call.args[0] for call in catalog.expand.await_args_list} == {NCS.id, ONET.id}
    planning = prompt(model.requests[0])["focused_evidence"]
    assert {source["id"] for source in planning["sources"]} >= {POSTING.id, NCS.id, ONET.id, UNIT.id, SKILL.id}
    final = prompt(model.requests[-1])
    assert {source["id"] for source in final["sources"]} >= {POSTING.id, NCS.id, ONET.id, UNIT.id, SKILL.id}
    mapping = next(link for link in final["relationships"] if link["id"] == MAPPING.id)
    assert mapping["source_quote"] == "데이터베이스" and mapping["target_quote"] == "database"
    assert mapping["mapping_version"] == "bilingual-technical-cues-v1" and mapping["review_status"] == "UNREVIEWED"
    assert any(link["kind"] == "INFERRED_POSTING_OCCUPATION" and link["catalog_pin"] == "classification-pin" for link in final["relationships"])
    result = next(event["data"] for event in events if event["type"] == "answer")
    assert {source["id"] for source in result["sources"]} == {UNIT.id, SKILL.id}
    assert MAPPING.id in next(action["edge_ids"] for action in result["actions"] if action["type"] == "highlight")
    assert [(event["name"], event["status"]) for event in events if event["type"] == "tool"].count(("expand_connections", "completed")) == 2


def test_stale_posting_classifications_are_read_without_attaching_current_skills():
    catalog, _ = fixtures(stale=True)
    model = NativeModel([])
    async def run():
        return [event async for event in ChatWorkspaceService(None, model, catalog).events(
            INITIAL, (LlmMessage("user", "이 공고의 직무 분류를 설명해줘"),), [POSTING.id], user_id=USER)]
    anyio.run(run)
    catalog.expand.assert_not_awaited()
    final = prompt(model.requests[-1])
    assert {source["id"] for source in final["sources"]} >= {POSTING.id, NCS.id, ONET.id}
    assert not any(source["id"] == UNIT.id for source in final["sources"])
    assert all(link["catalog_freshness"] == "STALE" for link in final["relationships"])


@pytest.mark.parametrize("target", [UNIT, GraphNode(id="postings:20010204", kind="posting_evidence", label="연결 공고", occupation_code="20010204")])
def test_focused_competency_mapping_and_posting_group_are_preloaded_without_model_tool_calls(target):
    found = EvidenceGraph(nodes=(UNIT, SKILL), edges=(MAPPING,)) if target == UNIT else EvidenceGraph(
        nodes=(target, POSTING), edges=(edge(target.id, POSTING.id, "INFERRED_POSTING_CONTEXT", "METADATA_INFERRED"),))
    catalog = SimpleNamespace(node=AsyncMock(return_value=EvidenceGraph(nodes=(target,))), expand=AsyncMock(return_value=found))
    model = NativeModel([])
    async def run():
        return [event async for event in ChatWorkspaceService(None, model, catalog).events(
            INITIAL, (LlmMessage("user", "이 항목의 연결 자료를 설명해줘"),), [target.id], user_id=USER)]
    anyio.run(run)
    catalog.expand.assert_awaited_once_with(target.id)
    final = prompt(model.requests[-1])
    assert {node.id for node in found.nodes} <= {source["id"] for source in final["sources"]}
    if target == UNIT:
        assert any(link["mapping_version"] == MAPPING.mapping_version for link in final["relationships"])


def test_failed_focused_catalog_read_keeps_classifications_and_other_catalog_evidence():
    catalog, _ = fixtures()
    original = catalog.expand.side_effect
    async def partial(identity):
        if identity == NCS.id:
            raise OSError("catalog connection lost")
        return await original(identity)
    catalog.expand.side_effect = partial
    model = NativeModel([])
    async def run():
        return [event async for event in ChatWorkspaceService(None, model, catalog).events(
            INITIAL, (LlmMessage("user", "공고 역량을 설명해줘"),), [POSTING.id], user_id=USER)]
    events = anyio.run(run)
    assert "FOCUSED_SOURCE_UNAVAILABLE" in next(event["data"]["warnings"] for event in events if event["type"] == "answer")
    assert SKILL.id in {source["id"] for source in prompt(model.requests[-1])["sources"]}
    assert events[-1] == {"type": "done"}


def test_cancelling_automatic_focus_reads_cancels_children_and_releases_capacity():
    async def run():
        catalog, _ = fixtures()
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def blocked(identity):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        catalog.expand.side_effect = blocked
        service = ChatWorkspaceService(None, NativeModel([]), catalog)
        async def collect():
            return [event async for event in service.events(INITIAL, (LlmMessage("user", "공고 역량"),), [POSTING.id], user_id=USER)]
        task = asyncio.create_task(collect())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set() and service._slots._value == 4
    anyio.run(run)


def test_focus_packet_reserves_room_for_both_occupation_skill_lists_and_omits_dangling_edges():
    ncs_skills = tuple(GraphNode(id=f"ncs:unit:{i}", kind="ncs_competency", label="NCS 역량") for i in range(40))
    graph = EvidenceGraph(nodes=(POSTING, NCS, ONET, *ncs_skills, SKILL), edges=(
        edge(POSTING.id, NCS.id, "INFERRED_POSTING_OCCUPATION", "METADATA_INFERRED"),
        edge(POSTING.id, ONET.id, "INFERRED_POSTING_OCCUPATION", "METADATA_INFERRED"),
        *(edge(skill.id, NCS.id, "CLASSIFIED_AS", "REFERENCE") for skill in ncs_skills),
        edge(ONET.id, SKILL.id, "ONET_ESSENTIAL_SKILL", "REFERENCE")))
    packet = focus_neighborhood(graph, [POSTING.id], 10)
    assert SKILL.id in {node.id for node in packet.nodes} and len(packet.nodes) == 10
    serialized = json.loads(tool_output(graph, (POSTING.id,)))
    known = {source["id"] for source in serialized["sources"]}
    assert SKILL.id in known
    assert all(link["source"] in known and link["target"] in known for link in serialized["relationships"])

from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_live_source_api import POSTING, Identity

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.live_source_models import CrosswalkResponse, PostingResponse
from jobtology_be.chat.catalog import SharedCatalogRetriever
from jobtology_be.chat.catalog_queries import NODE_ID
from jobtology_be.chat.crosswalk import crosswalk_graph
from jobtology_be.corpus.neo4j_client import Neo4jQueryResult
from jobtology_be.infrastructure.persistence.live_source_feed import LiveSourceReadError
from jobtology_be.main import create_app
from jobtology_be.settings import Settings


def mapping(status="PROPOSED", relation="RELATED"):
    return {
        "mapping_id": "mapping-one", "source": {"id": "ncs:unit:2001020413", "scheme": "NCS",
            "kind": "ncs_competency", "code": "2001020413_23v1", "name": "데이터베이스 구현",
            "description": "데이터를 저장하는 구조를 구현한다", "catalog_pin": "ncs-old", "content_hash": "abc"},
        "target": {"id": "onet:skill:programming", "scheme": "ONET", "kind": "onet_skill",
            "code": "2.B.3.e", "name": "Programming", "description": "Writing computer programs",
            "catalog_pin": "onet-31", "content_hash": "def"},
        "relation": relation, "status": status, "basis": "BILINGUAL_CUE",
        "evidence": {"cue_id": "programming", "source_quote": "구현한다", "target_quote": "computer programs"},
        "review": None if status == "PROPOSED" else {"reviewer_kind": "ASSISTANT", "reviewer": "reviewer-one",
            "note": "일부 작업만 관련됨", "reviewed_at": "2026-10-25T00:00:00Z"},
        "source_freshness": "STALE", "catalog_freshness": "CURRENT", "created_at": "2026-10-25T00:00:00Z",
    }


def response(*items):
    return CrosswalkResponse.model_validate({"contract_version": "jobtology-ncs-onet-crosswalk-v1",
        "basis": "CATALOG_CROSSWALK", "competency_evidence": False,
        "limit": 24, "offset": 0, "total": len(items), "items": list(items)})


def test_crosswalk_route_requires_authentication_and_binds_filters():
    feed = SimpleNamespace(crosswalk=AsyncMock(return_value=response(mapping())))
    with TestClient(create_app(Settings(), dependencies=ApiDependencies(live_source_feed=feed))) as client:
        assert client.get("/api/v2/live/crosswalk").status_code == 401
    feed.crosswalk.assert_not_awaited()
    with TestClient(create_app(Settings(), dependencies=ApiDependencies(identity_provider=Identity(), live_source_feed=feed))) as client:
        result = client.get("/api/v2/live/crosswalk", params={"node_id": "ncs:unit:2001020413", "limit": 24, "offset": 0})
    assert result.status_code == 200
    assert result.json()["competency_evidence"] is False
    assert result.json()["items"][0]["status"] == "PROPOSED"
    assert result.json()["items"][0]["source_freshness"] == "STALE"
    feed.crosswalk.assert_awaited_once_with("ncs:unit:2001020413", 24, 0)


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "node_id=", "node_id=a&node_id=b", "unknown=x"])
def test_crosswalk_rejects_invalid_queries_without_reading(query):
    feed = SimpleNamespace(crosswalk=AsyncMock())
    with TestClient(create_app(Settings(), dependencies=ApiDependencies(identity_provider=Identity(), live_source_feed=feed))) as client:
        assert client.get("/api/v2/live/crosswalk?" + query).status_code == 422
    feed.crosswalk.assert_not_awaited()


def test_crosswalk_proposals_do_not_turn_into_reviewed_requirements():
    graph = crosswalk_graph(response(mapping()))
    link = graph.edges[0]
    assert link.kind == "CROSSWALK_RELATED" and link.basis == "PROPOSED"
    assert link.review_status == "UNREVIEWED"
    assert link.source_freshness == "STALE" and link.catalog_freshness == "CURRENT"
    assert link.source_run_id == "ncs-old" and link.catalog_pin == "onet-31"
    assert "역량 자동 인정 없음" in link.explanation
    assert all(node.basis == "REFERENCE" for node in graph.nodes)


def test_reviewed_partial_mapping_keeps_actual_reviewer_and_rejected_mapping_is_hidden():
    graph = crosswalk_graph(response(mapping("APPROVED", "PARTIAL")))
    assert graph.edges[0].kind == "CROSSWALK_PARTIAL"
    assert graph.edges[0].basis == "REFERENCE"
    assert graph.edges[0].review_status == "ASSISTANT_REVIEWED"
    assert "일부 작업만 관련됨" in graph.edges[0].explanation
    assert not crosswalk_graph(response(mapping("REJECTED"))).edges


def test_live_software_proposal_preserves_unknown_creation_time_and_technology_kind():
    item = mapping()
    item["created_at"] = None
    item["target"] = {**item["target"], "kind": "technology", "name": "SQL"}
    data = response(item)
    assert data.items[0].created_at is None
    assert any(node.kind == "technology" and node.label == "SQL" for node in crosswalk_graph(data).nodes)


def test_crosswalk_contract_rejects_automatic_credit():
    payload = response().model_dump()
    payload["competency_evidence"] = True
    with pytest.raises(ValidationError):
        CrosswalkResponse.model_validate(payload)


def test_current_catalog_aliases_remain_connected_without_collapsing_historical_versions():
    item = mapping()
    item["source"] = {**item["source"], "id": "ncs:unit:2001020413_23v1"}
    data = response(item)
    assert crosswalk_graph(data, "ncs:unit:2001020413").edges[0].source == "ncs:unit:2001020413_23v1"
    item["source_freshness"] = "CURRENT"
    assert crosswalk_graph(response(item), "ncs:unit:2001020413").edges[0].source == "ncs:unit:2001020413"
    assert crosswalk_graph(response(item), "onet:skill:2.B.3.e").edges[0].target == "onet:skill:2.B.3.e"


def test_posting_keeps_source_categories_separate_from_inferred_classifications():
    inferred = {"posting_key": "jobkorea:one", "posting_identity": "source-one", "source_id": "jobkorea",
        "occupation_scheme": "NCS", "occupation_code": "20010204", "occupation_name": "DB엔지니어링",
        "association_id": "inference-one", "basis": "METADATA_INFERRED", "assertion_kind": "MODEL_INFERRED",
        "review_status": "UNREVIEWED", "stage": "STAGING", "confidence_state": "UNASSESSED",
        "source_freshness": "CURRENT", "catalog_freshness": "STALE",
        "source_pins": {"run_id": "source-pin"}, "catalog_pins": {"pin": "old-ncs"}}
    data = PostingResponse.model_validate({"contract_version": "hop-live-source-v1", "sources": [],
        "item": {**POSTING, "ncs_categories": [], "occupation_associations": [inferred]}})
    assert data.item.ncs_categories == []
    assert data.item.occupation_associations[0].catalog_freshness == "STALE"
    assert data.item.occupation_associations[0].competency_evidence is False
    assert data.item.occupation_associations[0].confidence is None


def test_catalog_expansion_adds_proposals_and_preserves_native_neighbors_on_reader_failure():
    async def run():
        async def read(query):
            if query.query_id == NODE_ID:
                return Neo4jQueryResult(fields=("node",), rows=(({"id": "ncs:unit:2001020413", "kind": "ncs_competency", "label": "DB 구현"},),), query_type=None)
            return Neo4jQueryResult(fields=("node", "source", "target", "kind"), rows=(), query_type=None)
        feed = SimpleNamespace(crosswalk=AsyncMock(return_value=response(mapping())))
        catalog = SharedCatalogRetriever(SimpleNamespace(execute=read), feed)
        graph = await catalog.expand("ncs:unit:2001020413")
        assert any(node.kind == "onet_skill" for node in graph.nodes)
        assert graph.edges[0].kind == "CROSSWALK_RELATED"
        assert next(node for node in graph.nodes if node.id == "ncs:unit:2001020413").label == "DB 구현"
        await catalog.expand("ncs:unit:2001020413")
        feed.crosswalk.assert_awaited_once()
        feed.crosswalk.side_effect = LiveSourceReadError(503)
        catalog._cache.clear()
        degraded = await catalog.expand("ncs:unit:2001020413")
        assert degraded.nodes[0].id == "ncs:unit:2001020413"
        assert "CROSSWALK_UNAVAILABLE" in degraded.warnings
    anyio.run(run)

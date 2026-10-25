from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest

from jobtology_be.api.live_source_models import NcsCategory, PostingOccupationAssociation
from jobtology_be.chat.catalog import SharedCatalogRetriever
from jobtology_be.chat.catalog_queries import EXPAND_ID, NODE_ID
from jobtology_be.chat.tools import ReadTools, tool_output
from jobtology_be.chat.workspace_models import merge_graphs
from jobtology_be.corpus.neo4j_client import Neo4jQueryResult


def posting():
    return SimpleNamespace(posting_id="jobkorea:abc", title="백엔드 개발자", organization_name="예시 기업", education="학력무관",
                           regions=["서울"], source_id="jobkorea", source_provenance=SimpleNamespace(run_id="current-source"))


def test_keyword_postings_have_search_context_edges_without_claiming_requirements():
    feed = SimpleNamespace(postings=AsyncMock(return_value=SimpleNamespace(items=[posting()], total=7)))
    graph = anyio.run(SharedCatalogRetriever(None, feed).search_postings, "개발")
    job = next(n for n in graph.nodes if n.kind == "posting")
    assert job.source_id == "jobkorea" and '예시 기업' in job.detail
    assert job.href == '/postings?posting=jobkorea%3Aabc'
    assert graph.truncated and len(graph.edges) == 1
    assert graph.edges[0].target == job.id
    assert graph.edges[0].kind == "SEARCH_RESULT" and graph.edges[0].basis == "APPLICATION_CONTEXT_SELECTION"


@pytest.mark.parametrize("code,group", [("20010202", "postings:20010202"), ("15-1252.00", "postings:onet:15-1252.00")])
def test_virtual_posting_groups_expand_real_postings_and_preserve_stale_status(code, group):
    item = SimpleNamespace(posting=posting(), association_id="association-1", source_freshness="CURRENT",
                           catalog_freshness="STALE", review_status="ASSISTANT_REVIEWED",
                           source_pins=SimpleNamespace(run_id="original-source"), catalog_pins=SimpleNamespace(pin="original-catalog"))
    feed = SimpleNamespace(occupation_context=AsyncMock(return_value=SimpleNamespace(items=[item], total=1)))
    catalog = SharedCatalogRetriever(None, feed)
    graph = anyio.run(catalog.expand, group)
    feed.occupation_context.assert_awaited_once_with([code], 6, 0)
    assert any(n.id == group for n in graph.nodes)
    assert graph.edges[0].catalog_freshness == "STALE"
    assert graph.edges[0].source_freshness == "CURRENT"
    assert graph.edges[0].basis == "METADATA_INFERRED" and graph.edges[0].review_status == "ASSISTANT_REVIEWED"
    assert graph.edges[0].source_run_id == "original-source" and graph.edges[0].catalog_pin == "original-catalog"
    assert graph.edges[0].kind != "REQUIRES"


def test_posting_group_rejects_arbitrary_paths_and_queries():
    feed = SimpleNamespace(occupation_context=AsyncMock())
    with pytest.raises(ValueError):
        anyio.run(SharedCatalogRetriever(None, feed).expand, "postings:https://private.example/")
    feed.occupation_context.assert_not_awaited()


def test_posting_status_and_deadline_survive_bounded_model_evidence():
    item = posting()
    item.organization_name = "긴 기업명" * 100
    item.source_status, item.closing_date = "CLOSED", "2026-10-01"
    node = SharedCatalogRetriever(None)._posting_node(item)
    assert '"source_status": "CLOSED"' in node.detail[:300]
    assert '"closing_date": "2026-10-01"' in node.detail[:300]


def classification(scheme="NCS", code="20010202", identity="ncs-one", freshness="CURRENT"):
    return PostingOccupationAssociation.model_validate({
        "posting_key": "jobkorea:abc", "posting_identity": "source-one", "source_id": "jobkorea",
        "occupation_scheme": scheme, "occupation_code": code, "occupation_name": "응용SW엔지니어링" if scheme == "NCS" else "Software Developers",
        "association_id": identity, "basis": "METADATA_INFERRED", "assertion_kind": "MODEL_INFERRED",
        "review_status": "UNREVIEWED", "stage": "STAGING", "confidence_state": "UNASSESSED",
        "source_freshness": "CURRENT", "catalog_freshness": freshness,
        "source_pins": {"run_id": "original-source"}, "catalog_pins": {"pin": "original-catalog"},
    })


def test_posting_expansion_connects_both_catalogs_and_preserves_inference_provenance():
    item = posting()
    item.occupation_associations = [classification(), classification("ONET", "15-1252.00", "onet-one", "STALE")]
    feed = SimpleNamespace(posting=AsyncMock(return_value=SimpleNamespace(item=item)))
    graph = anyio.run(SharedCatalogRetriever(None, feed).expand, "posting:jobkorea:abc")
    feed.posting.assert_awaited_once_with("jobkorea:abc")
    assert {node.id for node in graph.nodes} == {"posting:jobkorea:abc", "ncs:occupation:20010202", "urn:jobtology:onet:occupation:15-1252.00"}
    assert {link.target for link in graph.edges} == {"ncs:occupation:20010202", "urn:jobtology:onet:occupation:15-1252.00"}
    for link in graph.edges:
        assert link.source == "posting:jobkorea:abc" and link.basis == "METADATA_INFERRED"
        assert link.review_status == "UNREVIEWED" and link.kind == "INFERRED_POSTING_OCCUPATION"
        assert link.source_run_id == "original-source" and link.catalog_pin == "original-catalog"
    assert next(link for link in graph.edges if link.association_id == "onet-one").catalog_freshness == "STALE"
    # The association must not overwrite an existing group-to-posting edge with the same association ID.
    assert all(link.id != f"association:{link.association_id}" for link in graph.edges)
    assert not graph.warnings


def test_source_classification_is_distinct_from_inference_and_broad_fields_do_not_invent_occupations():
    item = posting()
    item.ncs_categories = [NcsCategory(code="20", name="정보통신"), NcsCategory(code="20010202", name="응용SW엔지니어링")]
    item.occupation_associations = [classification()]
    feed = SimpleNamespace(posting=AsyncMock(return_value=SimpleNamespace(item=item)))
    graph = anyio.run(SharedCatalogRetriever(None, feed).posting, "posting:jobkorea:abc")
    assert len(graph.nodes) == 2 and len(graph.edges) == 2
    source = next(link for link in graph.edges if link.kind == "SOURCE_NCS_CLASSIFICATION")
    assert source.basis == "REFERENCE" and source.source_run_id == "current-source"
    assert source.catalog_freshness == "UNKNOWN"
    assert not any(node.id.endswith(":20") for node in graph.nodes)


def test_unclassified_posting_keeps_metadata_and_reports_missing_links():
    feed = SimpleNamespace(posting=AsyncMock(return_value=SimpleNamespace(item=posting())))
    graph = anyio.run(SharedCatalogRetriever(None, feed).expand, "posting:jobkorea:abc")
    assert len(graph.nodes) == 1 and not graph.edges and not graph.truncated
    assert graph.warnings == ("POSTING_CLASSIFICATIONS_MISSING",)


def test_posting_classifications_page_without_repeating_or_losing_the_posting_anchor():
    async def run():
        item = posting()
        item.occupation_associations = [classification(code=str(20010000 + i), identity=f"association-{i:02}") for i in range(27)]
        feed = SimpleNamespace(posting=AsyncMock(return_value=SimpleNamespace(item=item)))
        catalog = SharedCatalogRetriever(None, feed)
        first, second = await catalog.expand("posting:jobkorea:abc"), await catalog.expand("posting:jobkorea:abc", 24)
        assert first.truncated and len(first.edges) == 24 and len(first.nodes) == 25
        assert not second.truncated and len(second.edges) == 3 and len(second.nodes) == 4
        assert not ({edge.id for edge in first.edges} & {edge.id for edge in second.edges})
        assert len(merge_graphs(first, second).edges) == 27
    anyio.run(run)


def test_llm_posting_tool_gets_classifications_and_can_expand_their_catalog_skills():
    async def run():
        item = posting()
        item.occupation_associations = [classification(), classification("ONET", "15-1252.00", "onet-one")]
        feed = SimpleNamespace(posting=AsyncMock(return_value=SimpleNamespace(item=item)))
        async def read(query):
            identity = query.parameters["node_id"]
            is_ncs = identity.startswith("ncs:")
            if query.query_id == NODE_ID:
                return Neo4jQueryResult(fields=("node",), rows=(({"id": identity, "kind": "ncs_occupation" if is_ncs else "onet_occupation", "label": "직무"},),), query_type=None)
            assert query.query_id == EXPAND_ID
            skill = {"id": "ncs:unit:2001020211_24v1" if is_ncs else "urn:jobtology:onet-detail:programming", "kind": "ncs_competency" if is_ncs else "onet_skill", "label": "서버 구현" if is_ncs else "Programming"}
            source, target = (skill["id"], identity) if is_ncs else (identity, skill["id"])
            return Neo4jQueryResult(fields=("node", "source", "target", "kind"), rows=((skill, source, target, "CLASSIFIED_AS" if is_ncs else "ONET_ESSENTIAL_SKILL"),), query_type=None)
        catalog = SharedCatalogRetriever(SimpleNamespace(execute=read), feed)
        anchor = await catalog.node("posting:jobkorea:abc")
        runtime = ReadTools(None, catalog, None, None)
        graph = await runtime.execute("get_posting", '{"node_id":"posting:jobkorea:abc"}', anchor)
        assert "INFERRED_POSTING_OCCUPATION" in tool_output(graph)
        for identity in ("ncs:occupation:20010202", "urn:jobtology:onet:occupation:15-1252.00"):
            found = await runtime.execute("expand_connections", '{"node_id":"' + identity + '"}', graph)
            graph = merge_graphs(graph, found)
        assert {node.kind for node in graph.nodes} >= {"posting", "ncs_competency", "onet_skill"}
        assert {edge.kind for edge in graph.edges} >= {"INFERRED_POSTING_OCCUPATION", "CLASSIFIED_AS", "ONET_ESSENTIAL_SKILL"}
        assert all(edge.kind != "REQUIRES" for edge in graph.edges)
    anyio.run(run)

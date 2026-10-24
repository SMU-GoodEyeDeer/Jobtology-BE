from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest

from jobtology_be.chat.catalog import SharedCatalogRetriever


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

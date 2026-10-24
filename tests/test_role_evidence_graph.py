from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jobtology_be.api.errors import register_error_handlers
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.role_evidence_graph import require_role_evidence_graph, router
from jobtology_be.application.requirement_metadata import RequirementMetadata
from jobtology_be.corpus.neo4j_client import (
    JsonValue,
    Neo4jQueryResult,
    Neo4jReadQuery,
    Neo4jTransportError,
)
from jobtology_be.corpus.neo4j_queries import (
    ROLE_CONTEXT_ONET_FIELDS,
    ROLE_CONTEXT_ONET_QUERY_ID,
    ROLE_EVIDENCE_NCS_FIELDS,
    ROLE_EVIDENCE_NCS_QUERY_ID,
    ROLE_EVIDENCE_ONET_FIELDS,
    ROLE_EVIDENCE_ONET_QUERY_ID,
)
from jobtology_be.corpus.neo4j_repository import Neo4jRepositoryResponseError
from jobtology_be.corpus.role_evidence_graph import Neo4jRoleEvidenceGraph
from jobtology_be.infrastructure.persistence.live_source_feed import LiveSourceReadError
from jobtology_be.product_roles.models import ProductRolePolicy

POLICY = ProductRolePolicy.model_validate_json(
    (Path(__file__).resolve().parents[1] / "config/product_roles/policy.v1.json").read_text()
)
PYTHON = {"id": "tech:python", "name": "Python", "in_demand": True}


class ContextFeed:
    def __init__(self):
        self.calls = []

    async def occupation_context(self, codes, limit, offset):
        self.calls.append((codes, limit, offset))
        return SimpleNamespace(summary=SimpleNamespace(by_occupation=[
            SimpleNamespace(occupation_scheme='ONET', occupation_code='15-1252.00', current_associations=11),
            SimpleNamespace(occupation_scheme='NCS', occupation_code='20010202', current_associations=99),
        ]))


def _ncs_row(code: str, name: str, onet_code: str) -> tuple[JsonValue, ...]:
    return (
        f"ncs:occupation:{code}", code, name, 12,
        [{"id": f"posting:{code}", "title": f"{name} 채용", "source_id": "jobkorea"}],
        [{"id": f"ncs:unit:{code}01_23v1", "code": f"{code}01_23v1", "name": "요구사항 확인", "links": 2}],
        [{"id": f"onet:{onet_code}", "code": onet_code, "name": f"O*NET {onet_code}", "shared": 7}],
        [{"id": "qnet:qualification:C290", "code": "C290", "name": "정보처리산업기사", "units": 7,
          "sessions": 3, "latest_session": "2026년 정기 기사 3회"}],
        [{"source_id": "jobkorea", "source_run_id": "run-jk", "catalog_run_id": "ncs-current",
          "source_current": True, "catalog_current": True, "review_status": "UNREVIEWED",
          "stage": "STAGING", "accepted": False, "postings": 12},
         {"source_id": "jobkorea", "source_run_id": "run-old", "catalog_run_id": "ncs-old",
          "source_current": False, "catalog_current": False, "review_status": "ASSISTANT_REVIEWED",
          "stage": "STAGING", "accepted": True, "postings": 3}], 3,
    )


def _onet_row(code: str) -> tuple[JsonValue, ...]:
    skill = {"id": "skill:critical", "name": "Critical Thinking", "importance": 3.88}
    return (f"onet:{code}", code, f"O*NET {code}", [skill], [PYTHON])


@dataclass(slots=True)
class FakeNeo4jClient:
    ncs_rows: tuple[tuple[JsonValue, ...], ...]
    onet_rows: tuple[tuple[JsonValue, ...], ...]
    queries: list[Neo4jReadQuery] = field(default_factory=list)
    context_rows: tuple[tuple[JsonValue, ...], ...] = ()

    async def execute(self, query: Neo4jReadQuery) -> Neo4jQueryResult:
        self.queries.append(query)
        if query.query_id == ROLE_EVIDENCE_NCS_QUERY_ID:
            return Neo4jQueryResult(ROLE_EVIDENCE_NCS_FIELDS, self.ncs_rows, "r")
        if query.query_id == ROLE_CONTEXT_ONET_QUERY_ID:
            return Neo4jQueryResult(ROLE_CONTEXT_ONET_FIELDS, self.context_rows, "r")
        assert query.query_id == ROLE_EVIDENCE_ONET_QUERY_ID
        return Neo4jQueryResult(ROLE_EVIDENCE_ONET_FIELDS, self.onet_rows, "r")


def _backend_client() -> FakeNeo4jClient:
    return FakeNeo4jClient(
        ncs_rows=(
            _ncs_row("20010202", "응용SW엔지니어링", "15-1252.00"),
            _ncs_row("20010204", "DB엔지니어링", "15-1242.00"),
        ),
        onet_rows=(_onet_row("15-1242.00"), _onet_row("15-1252.00")),
    )


def test_role_graph_links_policy_ncs_codes_through_postings_to_onet_details() -> None:
    # Given
    client = _backend_client()
    source = Neo4jRoleEvidenceGraph(policy=POLICY, client=client)

    # When
    graph = anyio.run(source.graph, "BACKEND_DEVELOPER")

    # Then
    assert graph is not None
    assert client.queries[0].parameters["codes"] == ["20010202", "20010204"]
    assert client.queries[1].parameters["codes"] == ["15-1252.00"]
    assert client.queries[2].parameters["ids"] == ["onet:15-1242.00", "onet:15-1252.00"]
    kinds = {node.id: node.kind for node in graph.nodes}
    assert kinds["role:BACKEND_DEVELOPER"] == "role"
    assert kinds["ncs:occupation:20010202"] == "ncs_occupation"
    assert kinds["postings:20010202"] == "posting_evidence"
    nodes = {node.id: node for node in graph.nodes}
    assert nodes["postings:20010202"].ncs_category == "20010202"
    assert nodes["ncs:occupation:20010202"].ncs_category == "20010202"
    unit = nodes["ncs:unit:2001020201_23v1"]
    assert unit.ncs_base_code == "2001020201"
    assert unit.requirement_key is None  # Source evidence alone is not a reviewed requirement.
    assert nodes["qnet:qualification:C290"].qualification_code == "C290"
    assert kinds["onet:15-1252.00"] == "onet_occupation"
    assert len([node for node in graph.nodes if node.id == "tech:python"]) == 1
    targets = {(edge.source, edge.target, edge.kind) for edge in graph.edges}
    assert ("role:BACKEND_DEVELOPER", "ncs:occupation:20010202", "MAPPED_TO") in targets
    assert ("ncs:occupation:20010202", "onet:15-1252.00", "MATCHED_ONET") in targets
    assert ("onet:15-1242.00", "tech:python", "HOT_TECHNOLOGY") in targets
    assert ("ncs:occupation:20010202", "qnet:qualification:C290", "QUALIFIED_BY") in targets
    assert ("ncs:occupation:20010204", "qnet:qualification:C290", "QUALIFIED_BY") in targets
    assert kinds["qnet:qualification:C290"] == "qualification"
    assert ("onet:15-1252.00", "tech:python", "HOT_TECHNOLOGY") in targets


def test_graph_never_links_details_from_another_revision_of_the_same_onet_code() -> None:
    client = _backend_client()
    current = _onet_row("15-1252.00")
    client.onet_rows = (("onet:old-revision", *current[1:]),)

    graph = anyio.run(Neo4jRoleEvidenceGraph(policy=POLICY, client=client).graph,
                      "BACKEND_DEVELOPER")

    assert graph is not None
    node_ids = {node.id for node in graph.nodes}
    assert "onet:15-1252.00" in node_ids
    assert "onet:old-revision" not in node_ids
    assert all(edge.source in node_ids and edge.target in node_ids for edge in graph.edges)
    assert not any(edge.kind in {"ESSENTIAL_SKILL", "HOT_TECHNOLOGY"} for edge in graph.edges)


def test_current_onet_context_survives_when_all_ncs_posting_mappings_are_stale() -> None:
    client = _backend_client()
    ncs = list(client.ncs_rows[0])
    ncs[3:5] = [0, []]
    ncs[6] = []
    ncs[8] = [ncs[8][1]]
    client.ncs_rows = (tuple(ncs),)
    client.onet_rows = (_onet_row("15-1252.00"),)
    client.context_rows = (("onet:15-1252.00", "15-1252.00", "Software Developers"),)
    feed = ContextFeed()
    graph = anyio.run(Neo4jRoleEvidenceGraph(policy=POLICY, client=client, context_feed=feed).graph, "BACKEND_DEVELOPER")
    nodes = {node.id: node for node in graph.nodes}
    assert nodes['ncs:occupation:20010202'].weight == 0
    assert nodes['ncs:occupation:20010202'].excluded_associations == 3
    assert nodes['postings:20010202'].weight == 0
    assert nodes['postings:20010202'].excluded_associations == 3
    assert nodes['postings:20010202'].label == '과거 공고 연결 3건'
    assert '과거 연결' in nodes['postings:20010202'].detail
    assert nodes['onet:15-1252.00'].basis == 'APPLICATION_CONTEXT_SELECTION'
    assert nodes['onet:15-1252.00'].requirement_key is None
    assert nodes['postings:onet:15-1252.00'].occupation_code == '15-1252.00'
    assert nodes['postings:onet:15-1252.00'].weight == 11
    assert not graph.warnings
    assert feed.calls == [(['15-1252.00'], 1, 0)]
    assert 'skill:critical' in nodes and 'tech:python' in nodes
    assert not any(edge.kind == 'MATCHED_ONET' for edge in graph.edges)
    assert any(edge.kind == 'CONTEXT_FOR' and edge.basis == 'APPLICATION_CONTEXT_SELECTION' for edge in graph.edges)
    assert all(edge.source in nodes and edge.target in nodes for edge in graph.edges)


def test_context_and_co_matched_onet_nodes_share_one_identity_and_detail_branch() -> None:
    client = _backend_client()
    client.context_rows = (("onet:15-1252.00", "15-1252.00", "Software Developers"),)
    graph = anyio.run(Neo4jRoleEvidenceGraph(policy=POLICY, client=client, context_feed=ContextFeed()).graph, "BACKEND_DEVELOPER")
    assert len([node for node in graph.nodes if node.id == 'onet:15-1252.00']) == 1
    assert len([edge for edge in graph.edges if edge.source == 'onet:15-1252.00' and edge.kind == 'HOT_TECHNOLOGY']) == 1
    assert sum(edge.kind == 'MATCHED_ONET' for edge in graph.edges) == 2
    assert sum(edge.kind == 'CONTEXT_FOR' for edge in graph.edges) == 1


@pytest.mark.parametrize('configured', [False, True])
def test_posting_feed_unavailability_keeps_catalog_nodes_with_an_explicit_warning(configured):
    class UnavailableFeed:
        async def occupation_context(self, *args):
            raise LiveSourceReadError(503)
    client = _backend_client()
    client.context_rows = (("onet:15-1252.00", "15-1252.00", "Software Developers"),)
    graph = anyio.run(Neo4jRoleEvidenceGraph(
        policy=POLICY, client=client, context_feed=UnavailableFeed() if configured else None,
    ).graph, "BACKEND_DEVELOPER")
    assert graph.warnings == ('POSTING_CONTEXT_UNAVAILABLE',)
    assert any(node.kind == 'onet_occupation' for node in graph.nodes)
    assert not any(node.id.startswith('postings:onet:') for node in graph.nodes)


def test_graph_deep_link_requirement_uses_verified_role_metadata() -> None:
    class Metadata:
        def lookup(self, requirement_key: str) -> RequirementMetadata | None:
            if requirement_key == "BACKEND_DEVELOPER:2001020201":
                return RequirementMetadata(
                    ncs_level=4, demand_pct=None, estimated_hours=20,
                    hours_basis="ESTIMATED", ncs_base_code="2001020201",
                )
            return None

    graph = anyio.run(Neo4jRoleEvidenceGraph(
        policy=POLICY, client=_backend_client(), requirement_metadata=Metadata(),
    ).graph, "BACKEND_DEVELOPER")
    assert graph is not None
    nodes = {node.id: node for node in graph.nodes}
    assert nodes["ncs:unit:2001020201_23v1"].requirement_key == "BACKEND_DEVELOPER:2001020201"
    assert nodes["ncs:unit:2001020401_23v1"].requirement_key is None


def test_graph_discloses_historical_inference_without_counting_it_as_current_evidence() -> None:
    graph = anyio.run(Neo4jRoleEvidenceGraph(policy=POLICY, client=_backend_client()).graph,
                      "BACKEND_DEVELOPER")
    assert graph is not None
    node = next(node for node in graph.nodes if node.id == "ncs:occupation:20010202")
    assert node.excluded_associations == 3
    assert node.evidence_sources[1].catalog_current is False
    assert node.evidence_sources[1].review_status == "ASSISTANT_REVIEWED"
    edge = next(edge for edge in graph.edges if edge.kind == "EVIDENCED_BY")
    assert edge.basis == "METADATA_INFERRED"
    assert len(edge.evidence_sources) == 1
    assert edge.evidence_sources[0].review_status == "UNREVIEWED"
    assert edge.evidence_sources[0].accepted is False
    assert edge.evidence_sources[0].confidence is None


def test_unknown_role_returns_none_without_querying_neo4j() -> None:
    # Given
    client = _backend_client()

    # When
    graph = anyio.run(Neo4jRoleEvidenceGraph(policy=POLICY, client=client).graph, "UNKNOWN")

    # Then
    assert graph is None
    assert client.queries == []


def test_malformed_neo4j_row_fails_closed() -> None:
    # Given
    client = FakeNeo4jClient(ncs_rows=(("id", "code", "name", "many", [], [], [], []),), onet_rows=())

    # When / Then
    with pytest.raises(Neo4jRepositoryResponseError):
        _ = anyio.run(Neo4jRoleEvidenceGraph(policy=POLICY, client=client).graph, "DATA_ANALYST")


@dataclass(frozen=True, slots=True)
class FailingClient:
    async def execute(self, query: Neo4jReadQuery) -> Neo4jQueryResult:
        _ = query
        raise Neo4jTransportError()


def _app(source: Neo4jRoleEvidenceGraph | None, *, authenticated: bool = True) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api/v2")
    if authenticated:
        async def principal() -> AuthenticatedPrincipal:
            return AuthenticatedPrincipal(user_id=uuid4())

        app.dependency_overrides[require_authenticated_principal] = principal
    if source is not None:
        app.dependency_overrides[require_role_evidence_graph] = lambda: source
    return app


def test_route_returns_graph_and_maps_unknown_role_to_not_found() -> None:
    # Given
    app = _app(Neo4jRoleEvidenceGraph(policy=POLICY, client=_backend_client()))

    # When
    with TestClient(app) as client:
        found = client.get("/api/v2/roles/BACKEND_DEVELOPER/evidence-graph")
        missing = client.get("/api/v2/roles/UNKNOWN/evidence-graph")

    # Then
    assert found.status_code == 200
    assert found.json()["name"] == "백엔드 개발자"
    assert missing.status_code == 404


@pytest.mark.parametrize(
    ("source", "authenticated", "status"),
    [
        (Neo4jRoleEvidenceGraph(policy=POLICY, client=FailingClient()), True, 503),
        (None, True, 503),
        (Neo4jRoleEvidenceGraph(policy=POLICY, client=FailingClient()), False, 401),
    ],
)
def test_route_fails_closed(
    source: Neo4jRoleEvidenceGraph | None, authenticated: bool, status: int
) -> None:
    # Given
    app = _app(source, authenticated=authenticated)

    # When
    with TestClient(app) as client:
        response = client.get("/api/v2/roles/AI_ENGINEER/evidence-graph")

    # Then
    assert response.status_code == status

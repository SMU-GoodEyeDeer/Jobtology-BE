from dataclasses import dataclass, field
from pathlib import Path
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
    ROLE_EVIDENCE_NCS_FIELDS,
    ROLE_EVIDENCE_NCS_QUERY_ID,
    ROLE_EVIDENCE_ONET_FIELDS,
    ROLE_EVIDENCE_ONET_QUERY_ID,
)
from jobtology_be.corpus.neo4j_repository import Neo4jRepositoryResponseError
from jobtology_be.corpus.role_evidence_graph import Neo4jRoleEvidenceGraph
from jobtology_be.product_roles.models import ProductRolePolicy

POLICY = ProductRolePolicy.model_validate_json(
    (Path(__file__).resolve().parents[1] / "config/product_roles/policy.v1.json").read_text()
)
PYTHON = {"id": "tech:python", "name": "Python", "in_demand": True}


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

    async def execute(self, query: Neo4jReadQuery) -> Neo4jQueryResult:
        self.queries.append(query)
        if query.query_id == ROLE_EVIDENCE_NCS_QUERY_ID:
            return Neo4jQueryResult(ROLE_EVIDENCE_NCS_FIELDS, self.ncs_rows, "r")
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
    assert client.queries[1].parameters["ids"] == ["onet:15-1242.00", "onet:15-1252.00"]
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

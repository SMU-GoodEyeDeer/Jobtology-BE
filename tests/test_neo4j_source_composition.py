from dataclasses import dataclass, field
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.api.neo4j_catalog import require_neo4j_catalog_queries
from jobtology_be.corpus.neo4j_models import (
    Neo4jEnrichment,
    Neo4jOccupationNode,
    Neo4jPublication,
)
from jobtology_be.corpus.neo4j_repository import (
    Neo4jCorpusRepository,
    Neo4jNcsAlignmentWithCompetency,
    Neo4jPagination,
)
from jobtology_be.corpus.source_availability import Available, SourceCapabilities, Unavailable
from jobtology_be.corpus.source_factory import ConfiguredNeo4jCatalog
from jobtology_be.main import create_app
from jobtology_be.settings import Settings


@dataclass(frozen=True, slots=True)
class StaticIdentityProvider:
    user_id: UUID

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


@dataclass(frozen=True, slots=True)
class RecordingNativeRepository:
    occupation_pages: list[Neo4jPagination] = field(default_factory=list)
    publication_pages: list[Neo4jPagination] = field(default_factory=list)
    alignment_pages: list[Neo4jPagination] = field(default_factory=list)

    async def list_occupations(
        self, *, page: Neo4jPagination | None = None
    ) -> tuple[Neo4jOccupationNode, ...]:
        assert page is not None
        self.occupation_pages.append(page)
        return ()

    async def list_publications(
        self, *, page: Neo4jPagination | None = None
    ) -> tuple[Neo4jPublication, ...]:
        assert page is not None
        self.publication_pages.append(page)
        return ()

    async def list_alignments(
        self,
        publication_id: str,
        *,
        page: Neo4jPagination | None = None,
    ) -> tuple[Neo4jNcsAlignmentWithCompetency, ...]:
        assert publication_id == "publication-1"
        assert page is not None
        self.alignment_pages.append(page)
        return ()

    async def get_enrichment(
        self, publication_id: str, enrichment_id: str
    ) -> Neo4jEnrichment | None:
        assert publication_id == "publication-1"
        assert enrichment_id == "enrichment-1"
        return None


@dataclass(slots=True)
class RecordingNeo4jSource:
    native_catalog: ConfiguredNeo4jCatalog
    closed: bool = False

    async def aclose(self) -> None:
        self.closed = True


def _capabilities() -> SourceCapabilities:
    unavailable = Unavailable(status="UNAVAILABLE", reason="UNVERIFIED_SOURCE_CONTRACT")
    return SourceCapabilities(
        source="neo4j_query_api",
        catalog=Available(status="AVAILABLE"),
        editorial_analysis=unavailable,
        route_planning=unavailable,
    )


def _neo4j_settings() -> Settings:
    return Settings(
        corpus_source="neo4j_query_api",
        db_link=SecretStr("graph.example.test:7687"),
        db_password=SecretStr("synthetic-query-api-password"),
        db_protocol="bolt+s://",
    )


def _dependencies() -> ApiDependencies:
    return ApiDependencies(
        identity_provider=StaticIdentityProvider(UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4"))
    )


def test_configured_neo4j_source_exposes_the_native_catalog_dependency() -> None:
    # Given
    app = create_app(_neo4j_settings(), dependencies=_dependencies())

    # When
    catalog = app.dependency_overrides[require_neo4j_catalog_queries]()
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")

    # Then
    assert response.status_code == 200
    assert isinstance(catalog, ConfiguredNeo4jCatalog)
    assert catalog.capabilities.source == "neo4j_query_api"
    assert catalog.capabilities.catalog.status == "AVAILABLE"
    assert catalog.capabilities.editorial_analysis.status == "UNAVAILABLE"
    assert catalog.capabilities.route_planning.status == "UNAVAILABLE"


def test_root_neo4j_composition_binds_the_production_repository_without_a_query() -> None:
    # Given
    app = create_app(_neo4j_settings(), dependencies=_dependencies())

    # When
    catalog = app.dependency_overrides[require_neo4j_catalog_queries]()
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")

    # Then
    assert response.status_code == 200
    assert isinstance(catalog, ConfiguredNeo4jCatalog)
    assert isinstance(catalog._repository, Neo4jCorpusRepository)


def test_configured_neo4j_source_forwards_v2_pages_to_the_native_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    repository = RecordingNativeRepository()
    catalog = ConfiguredNeo4jCatalog(capabilities=_capabilities(), _repository=repository)
    source = RecordingNeo4jSource(native_catalog=catalog)

    def build_source(_: Settings) -> RecordingNeo4jSource:
        return source

    monkeypatch.setattr("jobtology_be.main.build_configured_corpus_source", build_source)
    app = create_app(_neo4j_settings(), dependencies=_dependencies())

    # When
    with TestClient(app) as client:
        occupations = client.get("/api/v2/occupations", params={"limit": 2, "offset": 3})
        publications = client.get("/api/v2/publications", params={"limit": 4, "offset": 5})
        alignments = client.get(
            "/api/v2/publications/publication-1/alignments",
            params={"limit": 6, "offset": 7},
        )

    # Then
    assert occupations.status_code == 200
    assert publications.status_code == 200
    assert alignments.status_code == 200
    assert repository.occupation_pages == [Neo4jPagination(limit=2, offset=3)]
    assert repository.publication_pages == [Neo4jPagination(limit=4, offset=5)]
    assert repository.alignment_pages == [Neo4jPagination(limit=6, offset=7)]
    assert source.closed

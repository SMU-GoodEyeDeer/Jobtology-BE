from dataclasses import dataclass, field
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.errors import register_error_handlers
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.neo4j_catalog import require_neo4j_catalog_queries, router
from jobtology_be.corpus.neo4j_client import Neo4jQueryApiError, Neo4jTransportError
from jobtology_be.corpus.neo4j_models import (
    Neo4jNcsAlignment,
    Neo4jNcsCompetencyNode,
    Neo4jOccupationNode,
    Neo4jPublication,
)
from jobtology_be.corpus.neo4j_repository import (
    Neo4jNcsAlignmentSource,
    Neo4jPagination,
    Neo4jPublicationNotFoundError,
    Neo4jRepositoryRequestError,
    Neo4jRepositoryResponseError,
)
from jobtology_be.corpus.source_availability import Available, SourceCapabilities, Unavailable
from jobtology_be.main import create_app
from jobtology_be.settings import Settings


@dataclass(frozen=True, slots=True)
class StaticAlignment:
    source_enrichment: Neo4jNcsAlignmentSource
    alignment: Neo4jNcsAlignment
    competency: Neo4jNcsCompetencyNode


@dataclass(frozen=True, slots=True)
class StaticNativeCatalog:
    capabilities: SourceCapabilities
    occupations: tuple[Neo4jOccupationNode, ...]
    publications: tuple[Neo4jPublication, ...]
    alignments: tuple[StaticAlignment, ...]
    failure: Neo4jQueryApiError | None = None
    occupation_pages: list[Neo4jPagination] = field(default_factory=list, repr=False)
    publication_pages: list[Neo4jPagination] = field(default_factory=list, repr=False)
    alignment_pages: list[Neo4jPagination] = field(default_factory=list, repr=False)

    async def list_occupations(
        self, *, page: Neo4jPagination
    ) -> tuple[Neo4jOccupationNode, ...]:
        self.occupation_pages.append(page)
        if self.failure is not None:
            raise self.failure
        return self.occupations

    async def list_publications(self, *, page: Neo4jPagination) -> tuple[Neo4jPublication, ...]:
        self.publication_pages.append(page)
        if self.failure is not None:
            raise self.failure
        return self.publications

    async def list_alignments(
        self, publication_id: str, *, page: Neo4jPagination
    ) -> tuple[StaticAlignment, ...]:
        _ = publication_id
        self.alignment_pages.append(page)
        if self.failure is not None:
            raise self.failure
        return self.alignments


@dataclass(frozen=True, slots=True)
class StaticIdentityProvider:
    user_id: UUID

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


def _available_capabilities() -> SourceCapabilities:
    unavailable = Unavailable(status="UNAVAILABLE", reason="UNVERIFIED_SOURCE_CONTRACT")
    return SourceCapabilities(
        source="neo4j_query_api",
        catalog=Available(status="AVAILABLE"),
        editorial_analysis=unavailable,
        route_planning=unavailable,
    )


def _catalog(*, failure: Neo4jQueryApiError | None = None) -> StaticNativeCatalog:
    competency = Neo4jNcsCompetencyNode.model_validate(
        {
            "id": "competency-node",
            "code": "NCS-001",
            "kind": "ncsCompetency",
            "name": "Source competency",
            "name_source_record_id": "private-record",
            "name_source_run_id": "private-run",
        }
    )
    return StaticNativeCatalog(
        capabilities=_available_capabilities(),
        occupations=(
            Neo4jOccupationNode.model_validate(
                {
                    "id": "occupation-node",
                    "code": "OCC-001",
                    "kind": "occupation",
                    "name": "Source occupation",
                    "name_source_record_id": "private-record",
                    "name_source_run_id": "private-run",
                }
            ),
        ),
        publications=(
            Neo4jPublication.model_validate(
                {
                    "id": "private-publication-node",
                    "publication_id": "publication-1",
                    "postings": 9,
                    "state": "READY",
                }
            ),
        ),
        alignments=(
            StaticAlignment(
                source_enrichment=Neo4jNcsAlignmentSource.model_validate(
                    {"id": "enrichment-1", "posting_id": "posting-1", "current": True}
                ),
                alignment=Neo4jNcsAlignment.model_validate(
                    {"accepted": True, "decision_id": 42, "publication_id": "publication-1"}
                ),
                competency=competency,
            ),
        ),
        failure=failure,
    )


def _app(*, catalog: StaticNativeCatalog | None = None, authenticated: bool = True) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api/v2")
    if authenticated:
        async def principal() -> AuthenticatedPrincipal:
            return AuthenticatedPrincipal(user_id=uuid4())

        app.dependency_overrides[require_authenticated_principal] = principal
    if catalog is not None:
        def queries() -> StaticNativeCatalog:
            return catalog

        app.dependency_overrides[require_neo4j_catalog_queries] = queries
    return app


def test_native_catalog_routes_expose_only_safe_source_fields() -> None:
    # Given
    app = _app(catalog=_catalog())

    # When
    with TestClient(app) as client:
        occupations = client.get("/api/v2/occupations")
        publications = client.get("/api/v2/publications")
        alignments = client.get("/api/v2/publications/publication-1/alignments")

    # Then
    assert occupations.status_code == 200
    assert occupations.json() == [
        {
            "id": "occupation-node",
            "code": "OCC-001",
            "kind": "occupation",
            "name": "Source occupation",
        }
    ]
    assert publications.status_code == 200
    assert publications.json() == [
        {
            "publication_id": "publication-1",
            "source_state": "READY",
            "capabilities": {
                "source": "neo4j_query_api",
                "catalog": {"status": "AVAILABLE"},
                "editorial_analysis": {
                    "status": "UNAVAILABLE",
                    "reason": "UNVERIFIED_SOURCE_CONTRACT",
                },
                "route_planning": {
                    "status": "UNAVAILABLE",
                    "reason": "UNVERIFIED_SOURCE_CONTRACT",
                },
            },
        }
    ]
    assert alignments.status_code == 200
    assert alignments.json() == [
        {
            "publication_id": "publication-1",
            "source_enrichment_id": "enrichment-1",
            "source_posting_id": "posting-1",
            "source_current": True,
            "accepted": True,
            "competency": {
                "id": "competency-node",
                "code": "NCS-001",
                "kind": "ncsCompetency",
                "name": "Source competency",
            },
        }
    ]


def test_native_catalog_rejects_unauthenticated_requests() -> None:
    # Given
    app = _app(catalog=_catalog(), authenticated=False)

    # When
    with TestClient(app) as client:
        response = client.get("/api/v2/occupations")

    # Then
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_native_catalog_fails_closed_when_not_configured() -> None:
    # Given
    app = _app()

    # When
    with TestClient(app) as client:
        response = client.get("/api/v2/occupations")

    # Then
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"


def test_native_catalog_maps_transport_failures_to_data_unavailable() -> None:
    # Given
    app = _app(catalog=_catalog(failure=Neo4jTransportError()))

    # When
    with TestClient(app) as client:
        response = client.get("/api/v2/publications")

    # Then
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"


def test_native_catalog_maps_source_proven_publication_absence_to_not_found() -> None:
    # Given
    app = _app(catalog=_catalog(failure=Neo4jPublicationNotFoundError()))

    # When
    with TestClient(app) as client:
        response = client.get("/api/v2/publications/absent-publication/alignments")

    # Then
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["error"]["message"] == "Not Found"


def test_native_catalog_forwards_validated_pagination_to_each_read() -> None:
    # Given
    catalog = _catalog()
    app = _app(catalog=catalog)

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
    assert catalog.occupation_pages == [Neo4jPagination(limit=2, offset=3)]
    assert catalog.publication_pages == [Neo4jPagination(limit=4, offset=5)]
    assert catalog.alignment_pages == [Neo4jPagination(limit=6, offset=7)]


def test_native_catalog_rejects_out_of_bound_pagination_before_querying() -> None:
    # Given
    catalog = _catalog()
    app = _app(catalog=catalog)

    # When
    with TestClient(app) as client:
        large_limit = client.get("/api/v2/occupations", params={"limit": 101})
        negative_offset = client.get("/api/v2/publications", params={"offset": -1})

    # Then
    assert large_limit.status_code == 422
    assert negative_offset.status_code == 422
    assert catalog.occupation_pages == []
    assert catalog.publication_pages == []


def test_native_catalog_maps_repository_request_errors_to_validation_errors() -> None:
    # Given
    app = _app(catalog=_catalog(failure=Neo4jRepositoryRequestError()))

    # When
    with TestClient(app) as client:
        response = client.get("/api/v2/publications")

    # Then
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_native_catalog_maps_repository_response_errors_to_data_unavailable() -> None:
    # Given
    app = _app(catalog=_catalog(failure=Neo4jRepositoryResponseError()))

    # When
    with TestClient(app) as client:
        response = client.get("/api/v2/occupations")

    # Then
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"
    assert "repository response" not in response.text


def test_native_catalog_fails_closed_when_repository_returns_another_publication() -> None:
    # Given
    source = _catalog()
    mismatched = StaticNativeCatalog(
        capabilities=source.capabilities,
        occupations=source.occupations,
        publications=source.publications,
        alignments=(
            StaticAlignment(
                source_enrichment=source.alignments[0].source_enrichment,
                alignment=Neo4jNcsAlignment.model_validate(
                    {"accepted": False, "decision_id": 43, "publication_id": "another-publication"}
                ),
                competency=source.alignments[0].competency,
            ),
        ),
    )
    app = _app(catalog=mismatched)

    # When
    with TestClient(app) as client:
        response = client.get("/api/v2/publications/publication-1/alignments")

    # Then
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"


def test_application_composition_mounts_an_injected_native_catalog() -> None:
    # Given
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(uuid4()),
            neo4j_catalog=_catalog(),
        ),
    )

    # When
    with TestClient(app) as client:
        response = client.get("/api/v2/occupations")

    # Then
    assert response.status_code == 200
    assert "/api/v2/occupations" in app.openapi()["paths"]

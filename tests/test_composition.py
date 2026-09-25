import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from jobtology_be.api.analyses import require_analysis_service
from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.api.m5_queries import require_m5_queries
from jobtology_be.api.preferences import require_preferences_service
from jobtology_be.api.profiles import require_profile_service
from jobtology_be.application.services.analyses import (
    AnalysisRequestCommand,
    PersistentAnalysisService,
)
from jobtology_be.application.services.analysis_context import SnapshotBackedAnalysisContextFactory
from jobtology_be.application.services.analysis_inputs import PostgresAnalysisContextInputSource
from jobtology_be.application.services.preferences import PersistentPreferencesService
from jobtology_be.application.services.profiles import PersistentProfileService
from jobtology_be.corpus.local_snapshot import LocalJsonPublishedCorpusSnapshotReader
from jobtology_be.infrastructure.persistence.contracts import (
    AnalysisRecomputeSubmission,
    RecomputeRequestSnapshot,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.m5_queries import PostgresM5Queries
from jobtology_be.main import _configured_analysis_context_factory, create_app
from jobtology_be.settings import Settings
from jobtology_be.workers.context import RecomputeContextDocument


@dataclass(frozen=True, slots=True)
class StaticAnalysisContextFactory:
    async def create_context(
        self, user_id: UUID, command: AnalysisRequestCommand
    ) -> RecomputeContextDocument:
        raise AssertionError(f"Unexpected context request for {user_id}:{command.goal_id}")


@dataclass(frozen=True, slots=True)
class StaticAnalysisSubmitter:
    async def submit_analysis_recompute(
        self, submission: AnalysisRecomputeSubmission
    ) -> RecomputeRequestSnapshot:
        raise AssertionError(f"Unexpected recompute request for {submission.goal_id}")


@dataclass(frozen=True, slots=True)
class StaticIdentityProvider:
    user_id: UUID

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


@dataclass(slots=True)
class RecordingCorpusSource:
    local_snapshot_reader: LocalJsonPublishedCorpusSnapshotReader | None
    native_catalog: None = None
    closed: bool = False

    async def aclose(self) -> None:
        self.closed = True


def _published_snapshot_document() -> str:
    return json.dumps(
        {
            "snapshots": [
                {
                    "occupation_id": "BACKEND_DEVELOPER",
                    "basis_version": "reviewed-v1",
                    "release": {
                        "release_id": "release-reviewed-v1",
                        "state": "PUBLISHED",
                        "reviewed_at": "2026-09-22T00:00:00+00:00",
                    },
                    "is_fixture": False,
                    "capability_entries": [],
                    "allowed_experience_codes": [],
                    "requirements": [],
                    "templates": [],
                }
            ]
        }
    )


def test_production_settings_require_database_url() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match="database URL"):
        Settings(_env_file=None, environment="production")


def test_database_url_composes_persistent_profile_service() -> None:
    # Given
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://jobtology:secret@localhost:5432/jobtology",
    )

    # When
    app = create_app(settings)

    # Then
    provider = app.dependency_overrides[require_profile_service]
    assert isinstance(provider(), PersistentProfileService)


def test_database_url_composes_and_exposes_persistent_preferences_service() -> None:
    # Given
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://jobtology:secret@localhost:5432/jobtology",
    )

    # When
    app = create_app(settings)

    # Then
    provider = app.dependency_overrides[require_preferences_service]
    assert isinstance(provider(), PersistentPreferencesService)
    assert "/api/v1/me/route-preferences" in app.openapi()["paths"]


def test_database_url_composes_and_exposes_m5_query_surfaces() -> None:
    # Given
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://jobtology:secret@localhost:5432/jobtology",
    )

    # When
    app = create_app(settings)

    # Then
    provider = app.dependency_overrides[require_m5_queries]
    assert isinstance(provider(), PostgresM5Queries)
    assert {
        "/api/v1/dashboard",
        "/api/v1/occupations",
        "/api/v1/route-proposals/{proposal_id}",
        "/api/v1/traces/{trace_id}",
    }.issubset(app.openapi()["paths"])


def test_database_configuration_does_not_fabricate_an_analysis_service_without_context() -> None:
    # Given
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://jobtology:secret@localhost:5432/jobtology",
    )

    # When
    app = create_app(settings)

    # Then
    assert require_analysis_service not in app.dependency_overrides


def test_explicit_context_factory_and_submitter_compose_the_persistent_analysis_service() -> None:
    # Given
    dependencies = ApiDependencies(
        analysis_context_factory=StaticAnalysisContextFactory(),
        analysis_recompute_submitter=StaticAnalysisSubmitter(),
    )

    # When
    app = create_app(Settings(_env_file=None, enable_fixtures=False), dependencies=dependencies)

    # Then
    provider = app.dependency_overrides[require_analysis_service]
    assert isinstance(provider(), PersistentAnalysisService)


def test_database_store_submits_when_an_explicit_context_factory_is_available() -> None:
    # Given
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://jobtology:secret@localhost:5432/jobtology",
    )

    # When
    app = create_app(
        settings,
        dependencies=ApiDependencies(analysis_context_factory=StaticAnalysisContextFactory()),
    )

    # Then
    provider = app.dependency_overrides[require_analysis_service]
    assert isinstance(provider(), PersistentAnalysisService)


def test_database_and_published_snapshot_compose_an_analysis_service(tmp_path: Path) -> None:
    # Given
    snapshot_path = tmp_path / "published-snapshots.json"
    _ = snapshot_path.write_text(_published_snapshot_document())
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://jobtology:secret@localhost:5432/jobtology",
        corpus_snapshot_path=snapshot_path,
    )

    # When
    app = create_app(settings)

    # Then
    provider = app.dependency_overrides[require_analysis_service]
    assert isinstance(provider(), PersistentAnalysisService)


def test_neo4j_source_composition_fails_closed_and_closes_its_resource(monkeypatch) -> None:
    # Given
    source = RecordingCorpusSource(local_snapshot_reader=None)

    def build_source(_: Settings) -> RecordingCorpusSource:
        return source

    monkeypatch.setattr(
        "jobtology_be.main.build_configured_corpus_source",
        build_source,
    )
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://jobtology:secret@localhost:5432/jobtology",
        corpus_source="neo4j_query_api",
        db_link=SecretStr("graph.example.test:7687"),
        db_password=SecretStr("synthetic-query-api-password"),
        db_protocol="bolt+s://",
    )

    # When
    app = create_app(
        settings,
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4"))
        ),
    )
    with TestClient(app) as client:
        liveness_response = client.get("/api/v1/health/live")
        occupations_response = client.get("/api/v1/occupations")

    # Then
    queries = app.dependency_overrides[require_m5_queries]()
    assert isinstance(queries, PostgresM5Queries)
    assert queries._snapshot_reader is None
    assert require_analysis_service not in app.dependency_overrides
    assert liveness_response.status_code == 200
    assert occupations_response.status_code == 503
    assert occupations_response.json()["error"]["code"] == "DATA_UNAVAILABLE"
    assert source.closed


def test_neo4j_source_lifecycle_does_not_depend_on_postgresql(monkeypatch) -> None:
    # Given
    source = RecordingCorpusSource(local_snapshot_reader=None)

    def build_source(_: Settings) -> RecordingCorpusSource:
        return source

    monkeypatch.setattr(
        "jobtology_be.main.build_configured_corpus_source",
        build_source,
    )
    settings = Settings(
        corpus_source="neo4j_query_api",
        db_link=SecretStr("graph.example.test:7687"),
        db_password=SecretStr("synthetic-query-api-password"),
        db_protocol="bolt+s://",
    )

    # When
    app = create_app(
        settings,
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4"))
        ),
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")

    # Then
    assert response.status_code == 200
    assert source.closed


def test_configured_snapshot_uses_postgres_analysis_inputs(tmp_path: Path) -> None:
    # Given
    snapshot_path = tmp_path / "published-snapshots.json"
    _ = snapshot_path.write_text(_published_snapshot_document())
    database = Database.create("postgresql+asyncpg://jobtology:secret@localhost:5432/jobtology")
    snapshot_reader = LocalJsonPublishedCorpusSnapshotReader.from_path(snapshot_path)

    # When
    factory = _configured_analysis_context_factory(database, snapshot_reader)

    # Then
    assert isinstance(factory, SnapshotBackedAnalysisContextFactory)
    assert isinstance(factory.source, PostgresAnalysisContextInputSource)


def test_lifespan_starts_when_client_context_is_entered() -> None:
    # Given
    app = create_app(Settings(_env_file=None, enable_fixtures=False))

    # When
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")

    # Then
    assert response.status_code == 200

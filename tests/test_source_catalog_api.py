import json
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import override
from uuid import uuid4

import anyio
import pytest
from asyncpg import PostgresError
from fastapi.testclient import TestClient
from pydantic import JsonValue, SecretStr
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.api.source_catalog_models import (
    CatalogEntities,
    CatalogEntity,
    CatalogRelations,
    CatalogSummary,
    EntityKind,
)
from jobtology_be.infrastructure.persistence.source_catalog import (
    CatalogReadError,
    PostgresSourceCatalog,
    catalog_error_status,
)
from jobtology_be.main import create_app
from jobtology_be.settings import Settings


@dataclass(frozen=True, slots=True)
class Identity:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=uuid4())


@dataclass(frozen=True, slots=True)
class Catalog:
    def summary_payload(self) -> dict[str, JsonValue]:
        return {"contract_version": "hop-catalog-source-v1", "release_id": "r1", "data_as_of": None,
                "manifest_hash": "hash", "source_profile": {"kind": "SOURCE_ONLY", "analysis_available": False,
                "capabilities": ["entities", "source_relations"]}, "entity_counts": {"occupation": 1},
                "posting_selection_outcomes": {"SELECTION_PENDING": 2}}

    async def summary(self, release_id: str | None) -> CatalogSummary:
        return CatalogSummary.model_validate(self.summary_payload())

    async def entities(
        self, release_id: str | None, kind: EntityKind | None, limit: int, offset: int,
    ) -> CatalogEntities:
        return CatalogEntities.model_validate({**self.summary_payload(), "entity_kind": kind, "limit": limit, "offset": offset,
                "items": [{"entity_id": "urn:job/occ", "kind": "occupation", "code": "occ",
                           "scheme_id": None, "revision_id": "rev", "name": "Occupation",
                           "payload_hash": "hash", "private": "secret"}]})

    async def entity(self, release_id: str | None, entity_id: str) -> CatalogEntity:
        return CatalogEntity.model_validate({**self.summary_payload(), "entity": {"entity_id": entity_id,
                "kind": "occupation", "code": "occ", "scheme_id": None, "revision_id": "rev",
                "name": "Occupation", "payload_hash": "hash", "schema_version": "v1",
                "source_facts": {"aliases": ["O"], "private": "secret"}}})

    async def relations(
        self, release_id: str | None, entity_id: str, limit: int, offset: int,
    ) -> CatalogRelations:
        return CatalogRelations.model_validate({**self.summary_payload(), "entity_id": entity_id,
                "limit": limit, "offset": offset, "items": []})


def test_catalog_is_authenticated_and_projects_only_allowlisted_fields() -> None:
    # Given an injected source reader but no active user session
    app = create_app(Settings(), dependencies=ApiDependencies(source_catalog=Catalog()))
    with TestClient(app) as client:
        # When querying both old and new product routes
        assert client.get("/api/v1/me/profile").status_code == 401
        assert client.get("/api/v2/catalog/summary").status_code == 401
        assert client.get("/api/v2/catalog/entities").status_code == 401

    # Given an authenticated principal
    app = create_app(Settings(), dependencies=ApiDependencies(identity_provider=Identity(), source_catalog=Catalog()))
    with TestClient(app) as client:
        # When querying a slash-bearing source ID and a known kind
        listing = client.get("/api/v2/catalog/occupations", params={"limit": 1})
        detail = client.get("/api/v2/catalog/entities/urn:job/occ")
        relations = client.get("/api/v2/catalog/relations", params={"entity_id": "urn:job/occ"})
        summary = client.get("/api/v2/catalog/summary")
        rejected = client.get("/api/v2/catalog/summary", params={"preview": "true"})
        # Then no private source fields or preview escape into the response
        assert listing.status_code == detail.status_code == relations.status_code == summary.status_code == 200
        assert listing.json()["entity_kind"] == "occupation"
        assert "private" not in listing.text
        assert detail.json()["entity"]["entity_id"] == "urn:job/occ"
        assert detail.json()["entity"]["source_facts"] == {"aliases": ["O"]}
        assert relations.json()["entity_id"] == "urn:job/occ"
        assert rejected.status_code == 422


@pytest.mark.parametrize("status", [404, 410, 422, 503])
def test_catalog_maps_known_reader_failures_without_leaking_details(status: int) -> None:
    # Given a reader that returns a classified failure
    @dataclass(frozen=True, slots=True)
    class FailedCatalog(Catalog):
        @override
        async def summary(self, release_id: str | None) -> CatalogSummary:
            raise CatalogReadError(status)

    app = create_app(Settings(), dependencies=ApiDependencies(
        identity_provider=Identity(), source_catalog=FailedCatalog(),
    ))
    # When the source gate rejects a read
    with TestClient(app) as client:
        response = client.get("/api/v2/catalog/summary")
    # Then it exposes only the standard envelope
    assert response.status_code == status
    assert "sql" not in response.text.lower()
    assert response.json()["error"]["code"] == {
        404: "NOT_FOUND", 410: "GONE", 422: "VALIDATION_ERROR", 503: "DATA_UNAVAILABLE",
    }[status]


def test_catalog_rejects_unknown_filters_and_enforces_pagination() -> None:
    # Given a real authenticated route with an injected reader
    app = create_app(Settings(), dependencies=ApiDependencies(
        identity_provider=Identity(), source_catalog=Catalog(),
    ))
    # When public inputs request preview, invalid kinds or oversized pages
    with TestClient(app) as client:
        assert client.get("/api/v2/catalog/entities", params={"kind": "unknown"}).status_code == 422
        assert client.get("/api/v2/catalog/entities", params={"limit": 101}).status_code == 422
        assert client.get("/api/v2/catalog/entities", params={"offset": -1}).status_code == 422
        assert client.get("/api/v2/catalog/relations", params={"entity_id": "id", "preview": "true"}).status_code == 422


@pytest.mark.parametrize(("path", "query"), [
    ("summary", "release_id=r1&release_id=preview"),
    ("entities", "limit=1&limit=100"),
    ("occupations", "release_id=r1&release_id=preview"),
    ("relations", "entity_id=first&entity_id=second"),
    ("entities/urn:job/occ", "release_id=r1&release_id=preview"),
])
def test_catalog_rejects_duplicate_query_keys(path: str, query: str) -> None:
    # Given an authenticated source reader and two values for one query key
    app = create_app(Settings(), dependencies=ApiDependencies(
        identity_provider=Identity(), source_catalog=Catalog(),
    ))
    # When the caller supplies an ambiguous query, then no value wins silently
    with TestClient(app) as client:
        response = client.get(f"/api/v2/catalog/{path}?{query}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("path", [
    "summary", "entities", "occupations", "relations",
    "entities/urn:job/occ",
])
@pytest.mark.parametrize("release_id", ["", "   "])
def test_catalog_rejects_blank_release_pin(path: str, release_id: str) -> None:
    # Given an authenticated reader and a present but blank release pin
    app = create_app(Settings(), dependencies=ApiDependencies(
        identity_provider=Identity(), source_catalog=Catalog(),
    ))
    # When the caller supplies it, then the pointer cannot silently replace it
    with TestClient(app) as client:
        params = {"release_id": release_id}
        if path == "relations":
            params["entity_id"] = "urn:job/occ"
        response = client.get(f"/api/v2/catalog/{path}", params=params)
    assert response.status_code == 422


def test_editorial_drafts_are_explicit_only_and_never_analysis_ready() -> None:
    # Given no draft file configured
    app = create_app(Settings(), dependencies=ApiDependencies(identity_provider=Identity()))
    with TestClient(app) as client:
        assert client.get("/api/v1/editorial/occupations").status_code == 503
    draft_path = Path(__file__).resolve().parents[1] / "config/editorial/four_roles.v1.json"
    app = create_app(Settings(editorial_draft_path=draft_path))
    with TestClient(app) as client:
        assert client.get("/api/v1/editorial/occupations").status_code == 401
    app = create_app(
        Settings(editorial_draft_path=draft_path),
        dependencies=ApiDependencies(identity_provider=Identity()),
    )
    # When an authenticated user reads the explicitly enabled draft
    with TestClient(app) as client:
        listing = client.get("/api/v1/editorial/occupations")
        detail = client.get("/api/v1/editorial/occupations/AI_ENGINEER")
        missing = client.get("/api/v1/editorial/occupations/UNKNOWN")
    # Then only draft projections are returned
    assert listing.status_code == detail.status_code == 200
    assert len(listing.json()) == 4
    assert all(item["status"] == "DRAFT" and item["analysis_ready"] is False for item in listing.json())
    assert detail.json()["analysis_ready"] is False
    assert missing.status_code == 404


def test_catalog_url_remains_secret_and_is_not_inferred_from_product_database() -> None:
    # Given a separate configured catalog DSN
    settings = Settings(catalog_database_url=SecretStr("postgresql+asyncpg://jobtology_catalog_reader:password@localhost/catalog"))
    # When settings are printed and the independent option is omitted
    assert "jobtology_catalog_reader:password" not in repr(settings)
    assert Settings().catalog_database_url is None


@pytest.mark.parametrize("url", [
    "postgresql+asyncpg://postgres:secret@localhost/catalog",
    "postgresql://jobtology_catalog_reader:secret@localhost/catalog",
    "invalid-private-url",
])
def test_catalog_configuration_requires_dedicated_async_reader(url: str) -> None:
    # Given an unsafe or unsupported catalog connection URL
    # When settings parse it, then no app can bind a privileged reader
    with pytest.raises(ValueError, match="Catalog connection"):
        _ = Settings(catalog_database_url=SecretStr(url))


@pytest.mark.parametrize(("message", "expected"), [
    ("CATALOG_RELEASE_NOT_APPROVED", 503),
    ("CATALOG_APPROVAL_STALE", 503),
    ("CATALOG_RELEASE_UNAVAILABLE", 410),
    ("CATALOG_ENTITY_NOT_IN_RELEASE", 404),
    ("INVALID_CATALOG_PAGE", 422),
    ("secret internal query", 503),
])
def test_pg_catalog_error_mapping_requires_exact_sqlstate_and_message(
    message: str, expected: int,
) -> None:
    # Given the driver's wrapped PostgreSQL exception
    cause = PostgresError.new({"M": message, "C": "P0001"})
    adapter = RuntimeError("driver wrapper")
    adapter.__cause__ = cause
    # When the error is classified, then unknown errors fail closed
    assert catalog_error_status(DBAPIError("private sql", {}, adapter)) == expected


def test_repository_uses_only_bound_function_calls_in_one_readonly_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a narrow fake at the SQL wire, returning a DB JSON document
    calls: list[tuple[str, dict[str, str | int | None] | None]] = []

    class Result:
        def scalar_one(self) -> str:
            return json.dumps(Catalog().summary_payload())

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(
            self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> bool:
            return False

    class Connection(Transaction):
        def begin(self):
            return Transaction()

        async def execute(
            self, statement: TextClause, params: dict[str, str | int | None] | None = None,
        ) -> Result:
            calls.append((str(statement), params))
            return Result()

    async def read() -> None:
        catalog = PostgresSourceCatalog.create(
            "postgresql+asyncpg://jobtology_catalog_reader@localhost/catalog"
        )
        result = await catalog.summary("release/with:colon")
        assert result.release_id == "r1"
        await catalog.dispose()

    # When the repository executes a source read
    monkeypatch.setattr(AsyncEngine, "connect", lambda _: Connection())
    anyio.run(read)
    # Then a readonly transaction precedes one bound catalog function invocation
    assert calls == [
        ("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY", None),
        ("SELECT CAST(catalog.catalog_summary_v1(:release_id) AS text)",
         {"release_id": "release/with:colon"}),
    ]


def test_catalog_pool_is_disposed_at_app_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given an explicitly configured reader URL and an owned engine
    disposed: list[bool] = []

    async def dispose(self: PostgresSourceCatalog) -> None:
        disposed.append(True)
        await self.engine.dispose()

    monkeypatch.setattr(PostgresSourceCatalog, "dispose", dispose)
    app = create_app(Settings(catalog_database_url=SecretStr(
        "postgresql+asyncpg://jobtology_catalog_reader@localhost/catalog"
    )))
    # When the application lifespan exits
    with TestClient(app) as client:
        assert client.get("/api/v2/catalog/summary").status_code == 401
    # Then the dedicated pool is cleaned up without contacting PostgreSQL
    assert disposed == [True]


def test_repository_maps_connection_timeout_to_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a driver connect timeout before a SQL transaction begins
    def timed_out_connect(self: AsyncEngine) -> None:
        raise TimeoutError

    monkeypatch.setattr(AsyncEngine, "connect", timed_out_connect)
    catalog = PostgresSourceCatalog.create(
        "postgresql+asyncpg://jobtology_catalog_reader@localhost/catalog"
    )

    async def read() -> None:
        # When reading, then no driver exception or private URL escapes
        with pytest.raises(CatalogReadError) as failure:
            _ = await catalog.summary(None)
        assert failure.value.status_code == 503
        await catalog.dispose()

    anyio.run(read)

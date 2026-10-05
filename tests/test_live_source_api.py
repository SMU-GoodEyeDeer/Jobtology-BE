import json
from dataclasses import dataclass, field
from datetime import date
from types import TracebackType
from uuid import uuid4

import anyio
import pytest
from asyncpg import PostgresError
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.api.live_source import require_live_source_feed
from jobtology_be.api.live_source_models import (
    ExamSessionsResponse,
    PostingResponse,
    PostingsResponse,
)
from jobtology_be.api.source_catalog import require_source_catalog
from jobtology_be.infrastructure.persistence.live_source_feed import (
    LiveSourceReadError,
    PostgresLiveSourceFeed,
    live_source_error_status,
)
from jobtology_be.infrastructure.persistence.source_catalog import PostgresSourceCatalog
from jobtology_be.main import create_app
from jobtology_be.settings import Settings


@dataclass(frozen=True, slots=True)
class Identity:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=uuid4())


POSTING = {
    "posting_id": "urn:job:one", "title": "Developer", "organization_code": "ORG",
    "organization_name": "Organization", "date_posted": "2026-10-01",
    "closing_date": None, "ongoing": True, "regions": ["Seoul"],
    "employment_types": ["regular"], "recruitment_type": None,
    "education": None, "ncs_categories": [{"code": "01", "name": "IT"}],
    "headcount": 2, "source_url": "https://example.org/job",
}
SOURCES = [{"source_id": "job_alio", "run_id": "ready-1", "data_as_of": "2026-10-01T00:00:00Z"}]
EXAM = {
    "qualification_code": "Q1", "qualification_name": "Qualification",
    "year": 2026, "round": 1, "category_code": None, "name": None,
    "written": {"registration_start": "2026-10-01", "registration_end": None,
                "exam_start": None, "exam_end": None, "result_date": None},
    "practical": {"registration_start": None, "registration_end": None,
                  "exam_start": None, "exam_end": None, "result_date": None},
}


@dataclass(frozen=True, slots=True)
class Feed:
    calls: list[tuple] = field(default_factory=list)
    failure: int | None = None

    async def postings(
        self, q: str | None, ncs_category: str | None, region: str | None,
        open_on: date | None, limit: int, offset: int,
    ) -> PostingsResponse:
        self.calls.append(("postings", q, ncs_category, region, open_on, limit, offset))
        if self.failure is not None:
            raise LiveSourceReadError(self.failure)
        return PostingsResponse.model_validate({
            "contract_version": "hop-live-source-v1", "sources": SOURCES,
            "filters": {"q": q, "ncs_category": ncs_category, "region": region,
                        "open_on": open_on.isoformat() if open_on else None},
            "limit": limit, "offset": offset, "total": 1, "items": [POSTING],
        })

    async def posting(self, posting_id: str) -> PostingResponse:
        self.calls.append(("posting", posting_id))
        if self.failure is not None:
            raise LiveSourceReadError(self.failure)
        return PostingResponse.model_validate({
            "contract_version": "hop-live-source-v1", "sources": SOURCES, "item": POSTING,
        })

    async def exam_sessions(
        self, qualification: str | None, from_date: date | None,
        to_date: date | None, limit: int, offset: int,
    ) -> ExamSessionsResponse:
        self.calls.append(("exams", qualification, from_date, to_date, limit, offset))
        if self.failure is not None:
            raise LiveSourceReadError(self.failure)
        return ExamSessionsResponse.model_validate({
            "contract_version": "hop-live-source-v1", "sources": SOURCES,
            "filters": {"qualification": qualification,
                        "from": from_date.isoformat() if from_date else None,
                        "to": to_date.isoformat() if to_date else None},
            "limit": limit, "offset": offset, "total": 1, "items": [EXAM],
        })


def test_live_routes_forward_filters_and_return_typed_payloads() -> None:
    # Given an authenticated principal and an in-memory feed
    feed = Feed()
    app = create_app(Settings(), dependencies=ApiDependencies(identity_provider=Identity(), live_source_feed=feed))
    # When each route is read
    with TestClient(app) as client:
        postings = client.get("/api/v2/live/postings", params={
            "q": "developer", "ncs_category": "01", "region": "Seoul",
            "open_on": "2026-10-05", "limit": 1, "offset": 2,
        })
        detail = client.get("/api/v2/live/postings/urn:job:one")
        exams = client.get("/api/v2/live/exam-sessions", params={
            "qualification": "Q1", "from": "2026-10-01", "to": "2026-12-31",
            "limit": 5, "offset": 3,
        })
    # Then the exact parameters and declared response fields are observable
    assert [postings.status_code, detail.status_code, exams.status_code] == [200, 200, 200]
    assert feed.calls == [
        ("postings", "developer", "01", "Seoul", date(2026, 10, 5), 1, 2),
        ("posting", "urn:job:one"),
        ("exams", "Q1", date(2026, 10, 1), date(2026, 12, 31), 5, 3),
    ]
    assert postings.json()["items"][0]["posting_id"] == "urn:job:one"
    assert detail.json()["item"]["title"] == "Developer"
    assert exams.json()["items"][0]["qualification_code"] == "Q1"


@pytest.mark.parametrize("path", ["postings", "postings/one", "exam-sessions"])
def test_live_requires_authentication(path: str) -> None:
    # Given a configured feed without identity
    app = create_app(Settings(), dependencies=ApiDependencies(live_source_feed=Feed()))
    # When reading any route, then authentication closes it first
    with TestClient(app) as client:
        response = client.get(f"/api/v2/live/{path}")
    assert response.status_code == 401


@pytest.mark.parametrize("path,query", [
    ("postings", "preview=true"), ("postings", "q=a&q=b"),
    ("postings", "open_on=tomorrow"), ("postings", "limit=0"),
    ("postings", "limit=101"), ("postings", "offset=-1"),
    ("postings/one", "unknown=1"), ("postings/one", "unknown=1&unknown=2"),
    ("exam-sessions", "from=invalid"), ("exam-sessions", "to=invalid"),
    ("exam-sessions", "from=2026-01-01&from=2026-02-01"),
    ("exam-sessions", "limit=101"), ("exam-sessions", "unknown=1"),
])
def test_live_rejects_invalid_query_without_reading(path: str, query: str) -> None:
    # Given a feed that records reads
    feed = Feed()
    app = create_app(Settings(), dependencies=ApiDependencies(identity_provider=Identity(), live_source_feed=feed))
    # When an ambiguous or invalid query arrives, then it is rejected before DB access
    with TestClient(app) as client:
        response = client.get(f"/api/v2/live/{path}?{query}")
    assert response.status_code == 422
    assert feed.calls == []


@pytest.mark.parametrize("status,code", [
    (404, "NOT_FOUND"), (422, "VALIDATION_ERROR"), (503, "DATA_UNAVAILABLE"),
])
def test_live_classified_errors_use_public_envelope(status: int, code: str) -> None:
    # Given a failed reader
    app = create_app(Settings(), dependencies=ApiDependencies(identity_provider=Identity(), live_source_feed=Feed(failure=status)))
    # When a detail is read, then no DB message leaks
    with TestClient(app) as client:
        response = client.get("/api/v2/live/postings/one")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_live_unconfigured_returns_503() -> None:
    # Given an authenticated app without the optional feed
    app = create_app(Settings(), dependencies=ApiDependencies(identity_provider=Identity()))
    # When reading, then it fails closed
    with TestClient(app) as client:
        response = client.get("/api/v2/live/postings")
    assert response.status_code == 503


def test_live_configured_reader_reuses_catalog_engine() -> None:
    # Given a configured restricted catalog DSN
    app = create_app(Settings(catalog_database_url=SecretStr(
        "postgresql+asyncpg://jobtology_catalog_reader@localhost/catalog"
    )))
    # When dependencies are composed, then live and sealed reads share one pool
    live = app.dependency_overrides[require_live_source_feed]()
    catalog = app.dependency_overrides[require_source_catalog]()
    assert isinstance(live, PostgresLiveSourceFeed)
    assert isinstance(catalog, PostgresSourceCatalog)
    assert live.engine is catalog.engine
    with TestClient(app) as client:
        assert client.get("/api/v2/live/postings").status_code == 401


@pytest.mark.parametrize("message,expected", [
    ("LIVE_SOURCE_UNAVAILABLE", 503), ("INVALID_LIVE_PAGE", 422),
    ("INVALID_LIVE_FILTER", 422), ("LIVE_POSTING_NOT_FOUND", 404),
    ("private SQL", 503),
])
def test_live_error_classification_needs_exact_sqlstate(message: str, expected: int) -> None:
    # Given a wrapped PostgreSQL error
    cause = PostgresError.new({"M": message, "C": "P0001"})
    adapter = RuntimeError("driver wrapper")
    adapter.__cause__ = cause
    # When classified, then only known P0001 messages get special status
    assert live_source_error_status(DBAPIError("private sql", {}, adapter)) == expected
    other = PostgresError.new({"M": message, "C": "XX000"})
    assert live_source_error_status(DBAPIError("private sql", {}, other)) == 503


def test_live_models_reject_wrong_types_and_dates() -> None:
    # Given otherwise valid source payloads
    payload = {"contract_version": "hop-live-source-v1", "sources": SOURCES, "item": POSTING}
    # When DB returns malformed fields, then strict parsing fails
    with pytest.raises(ValidationError):
        PostingResponse.model_validate({**payload, "item": {**POSTING, "headcount": "2"}})
    with pytest.raises(ValidationError):
        PostingResponse.model_validate({**payload, "item": {**POSTING, "date_posted": "Oct 1"}})


def test_live_adapter_binds_dates_and_reads_in_readonly_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a narrow in-memory SQL connection that captures statements and parameters
    calls: list[tuple[str, dict[str, str | int | date | None] | None]] = []

    class Result:
        def scalar_one(self) -> str:
            return json.dumps({
                "contract_version": "hop-live-source-v1", "sources": SOURCES,
                "filters": {"q": "Engineer", "ncs_category": None, "region": None,
                            "open_on": "2026-10-05"},
                "limit": 1, "offset": 2, "total": 1, "items": [POSTING],
            })

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
            self, statement: TextClause, params: dict[str, str | int | date | None] | None = None,
        ) -> Result:
            calls.append((str(statement), params))
            return Result()

    monkeypatch.setattr(AsyncEngine, "connect", lambda _: Connection())
    catalog = PostgresSourceCatalog.create(
        "postgresql+asyncpg://jobtology_catalog_reader@localhost/catalog"
    )

    async def read() -> None:
        try:
            feed = PostgresLiveSourceFeed(engine=catalog.engine)
            assert (await feed.postings("Engineer", None, None, date(2026, 10, 5), 1, 2)).total == 1
        finally:
            await catalog.dispose()

    # When the adapter reads the feed
    anyio.run(read)
    # Then one bound function call follows a read-only transaction declaration
    assert calls == [
        ("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY", None),
        ("SELECT CAST(catalog.live_postings_v1(:q, :ncs_category, :region, CAST(:open_on AS date), :limit, :offset) AS text)",
         {"q": "Engineer", "ncs_category": None, "region": None,
          "open_on": date(2026, 10, 5), "limit": 1, "offset": 2}),
    ]

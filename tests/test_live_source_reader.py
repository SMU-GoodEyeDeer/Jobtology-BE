import json
from datetime import date
from types import TracebackType

import anyio
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from jobtology_be.infrastructure.persistence.live_source_feed import PostgresLiveSourceFeed
from jobtology_be.infrastructure.persistence.source_catalog import PostgresSourceCatalog


def test_live_adapter_binds_dates_and_reads_in_readonly_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a narrow in-memory SQL connection that captures statements and parameters
    calls: list[tuple[str, dict[str, str | int | date | None] | None]] = []

    class Result:
        def scalar_one(self) -> str:
            return json.dumps({
                "contract_version": "hop-live-source-v1", "sources": [],
                "filters": {"q": "Engineer", "ncs_category": None, "region": None,
                            "open_on": "2026-10-05"},
                "limit": 1, "offset": 2, "total": 1, "items": [],
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

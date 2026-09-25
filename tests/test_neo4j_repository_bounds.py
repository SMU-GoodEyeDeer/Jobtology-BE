from hashlib import sha256

import anyio
import pytest

from jobtology_be.corpus.neo4j_client import Neo4jQueryResult, Neo4jReadQuery
from jobtology_be.corpus.neo4j_queries import ENRICHMENT_FIELDS, OCCUPATION_FIELDS
from jobtology_be.corpus.neo4j_repository import (
    Neo4jCorpusRepository,
    Neo4jDuplicateSourceIdentityError,
    Neo4jPagination,
    Neo4jRepositoryRequestError,
    Neo4jRepositoryResponseError,
)


class _StaticReadClient:
    _result: Neo4jQueryResult
    _queries: list[Neo4jReadQuery]

    def __init__(self, result: Neo4jQueryResult, queries: list[Neo4jReadQuery]) -> None:
        self._result = result
        self._queries = queries

    async def execute(self, query: Neo4jReadQuery) -> Neo4jQueryResult:
        self._queries.append(query)
        return self._result


def test_list_occupations_rejects_a_page_larger_than_the_client_row_bound() -> None:
    # Given
    executed_queries: list[Neo4jReadQuery] = []
    repository = Neo4jCorpusRepository(
        client=_StaticReadClient(
            result=Neo4jQueryResult(fields=OCCUPATION_FIELDS, rows=(), query_type="r"),
            queries=executed_queries,
        ),
        max_rows=5,
    )

    async def list_occupations() -> None:
        # When / Then
        with pytest.raises(Neo4jRepositoryRequestError):
            _ = await repository.list_occupations(page=Neo4jPagination(limit=6, offset=0))
        assert not executed_queries

    anyio.run(list_occupations)


def test_list_occupations_rejects_unexpected_response_columns_without_echoing_them() -> None:
    # Given
    secret_column = "private_reviewer"
    repository = Neo4jCorpusRepository(
        client=_StaticReadClient(
            result=Neo4jQueryResult(
                fields=(*OCCUPATION_FIELDS, secret_column),
                rows=(
                    (
                        "occupation-node-1",
                        "NCS-001",
                        "occupation",
                        "Source label",
                        "source-record-1",
                        "source-run-1",
                        "private-value",
                    ),
                ),
                query_type="r",
            ),
            queries=[],
        ),
        max_rows=50,
    )

    async def list_occupations() -> None:
        # When / Then
        with pytest.raises(Neo4jRepositoryResponseError) as raised:
            _ = await repository.list_occupations(page=Neo4jPagination(limit=20, offset=0))
        assert secret_column not in str(raised.value)

    anyio.run(list_occupations)


def test_list_occupations_rejects_duplicate_source_identities() -> None:
    # Given
    repository = Neo4jCorpusRepository(
        client=_StaticReadClient(
            result=Neo4jQueryResult(
                fields=OCCUPATION_FIELDS,
                rows=(
                    (
                        "occupation-node-1",
                        "NCS-001",
                        "occupation",
                        "First source label",
                        "source-record-1",
                        "source-run-1",
                    ),
                    (
                        "occupation-node-1",
                        "NCS-002",
                        "occupation",
                        "Second source label",
                        "source-record-2",
                        "source-run-2",
                    ),
                ),
                query_type="r",
            ),
            queries=[],
        ),
        max_rows=50,
    )

    async def list_occupations() -> None:
        # When / Then
        with pytest.raises(Neo4jDuplicateSourceIdentityError):
            _ = await repository.list_occupations(page=Neo4jPagination(limit=20, offset=0))

    anyio.run(list_occupations)


def test_list_occupations_uses_one_client_bounded_page_by_default() -> None:
    # Given
    executed_queries: list[Neo4jReadQuery] = []
    repository = Neo4jCorpusRepository(
        client=_StaticReadClient(
            result=Neo4jQueryResult(fields=OCCUPATION_FIELDS, rows=(), query_type="r"),
            queries=executed_queries,
        ),
        max_rows=5,
    )

    async def list_occupations() -> None:
        # When
        _ = await repository.list_occupations()

    anyio.run(list_occupations)

    # Then
    assert executed_queries[0].parameters == {"limit": 5, "offset": 0}


def test_list_occupations_rejects_more_rows_than_its_requested_page() -> None:
    # Given
    repository = Neo4jCorpusRepository(
        client=_StaticReadClient(
            result=Neo4jQueryResult(
                fields=OCCUPATION_FIELDS,
                rows=(
                    ("occupation-node-1", "NCS-001", "occupation", "One", "record-1", "run-1"),
                    ("occupation-node-2", "NCS-002", "occupation", "Two", "record-2", "run-2"),
                ),
                query_type="r",
            ),
            queries=[],
        ),
        max_rows=10,
    )

    async def list_occupations() -> None:
        # When / Then
        with pytest.raises(Neo4jRepositoryResponseError):
            _ = await repository.list_occupations(page=Neo4jPagination(limit=1, offset=0))

    anyio.run(list_occupations)


def test_get_enrichment_rejects_multiple_selected_rows_without_echoing_payload() -> None:
    # Given
    raw_payload = (
        '{"item_id":"item-1","posting_id":"posting-1","revision_id":"revision-1",'
        '"source_hash":"source-hash","extraction":{"duties_status":"complete",'
        '"extraction_scope":"scope","schema_version":"v1"}}'
    )
    repository = Neo4jCorpusRepository(
        client=_StaticReadClient(
            result=Neo4jQueryResult(
                fields=ENRICHMENT_FIELDS,
                rows=(
                    (
                        "enrichment-node-1",
                        "publication-1",
                        "posting-1",
                        True,
                        "private-manager",
                        "private-name",
                        raw_payload,
                        sha256(raw_payload.encode("utf-8")).hexdigest(),
                    ),
                    (
                        "enrichment-node-2",
                        "publication-1",
                        "posting-2",
                        True,
                        "private-manager",
                        "private-name",
                        raw_payload,
                        sha256(raw_payload.encode("utf-8")).hexdigest(),
                    ),
                ),
                query_type="r",
            ),
            queries=[],
        ),
        max_rows=10,
    )

    async def get_enrichment() -> None:
        # When / Then
        with pytest.raises(Neo4jDuplicateSourceIdentityError) as raised:
            _ = await repository.get_enrichment("publication-1", "enrichment-node-1")
        assert raw_payload not in str(raised.value)

    anyio.run(get_enrichment)

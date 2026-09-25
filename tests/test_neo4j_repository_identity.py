from collections.abc import Mapping
from hashlib import sha256

import anyio
import pytest

from jobtology_be.corpus.neo4j_client import (
    Neo4jQueryResult,
    Neo4jReadQuery,
    Neo4jReadQueryId,
)
from jobtology_be.corpus.neo4j_queries import (
    ALIGNMENT_FIELDS,
    ENRICHMENT_FIELDS,
    GET_ENRICHMENT_QUERY_ID,
    GET_PUBLICATION_BY_ID_QUERY_ID,
    LIST_ALIGNMENTS_QUERY_ID,
    PUBLICATION_FIELDS,
)
from jobtology_be.corpus.neo4j_repository import (
    Neo4jCorpusRepository,
    Neo4jPagination,
    Neo4jPublicationNotFoundError,
    Neo4jRepositoryResponseError,
)


class _RoutingReadClient:
    _results: Mapping[Neo4jReadQueryId, Neo4jQueryResult]
    _queries: list[Neo4jReadQuery]

    def __init__(
        self,
        results: Mapping[Neo4jReadQueryId, Neo4jQueryResult],
        queries: list[Neo4jReadQuery],
    ) -> None:
        self._results = results
        self._queries = queries

    async def execute(self, query: Neo4jReadQuery) -> Neo4jQueryResult:
        self._queries.append(query)
        return self._results[query.query_id]


def test_get_enrichment_rejects_a_response_that_does_not_echo_the_requested_identity() -> None:
    # Given
    raw_payload = (
        '{"item_id":"item-1","posting_id":"posting-1","revision_id":"revision-1",'
        '"source_hash":"source-hash","extraction":{"duties_status":"complete",'
        '"extraction_scope":"scope","schema_version":"v1"}}'
    )
    executed_queries: list[Neo4jReadQuery] = []
    repository = Neo4jCorpusRepository(
        client=_RoutingReadClient(
            results={
                GET_ENRICHMENT_QUERY_ID: Neo4jQueryResult(
                    fields=ENRICHMENT_FIELDS,
                    rows=(
                        (
                            "unexpected-enrichment",
                            "unexpected-publication",
                            "posting-1",
                            True,
                            "private-manager",
                            "private-name",
                            raw_payload,
                            sha256(raw_payload.encode("utf-8")).hexdigest(),
                        ),
                    ),
                    query_type="r",
                ),
            },
            queries=executed_queries,
        ),
        max_rows=10,
    )

    async def get_enrichment() -> None:
        # When / Then
        with pytest.raises(Neo4jRepositoryResponseError) as raised:
            _ = await repository.get_enrichment("publication-1", "enrichment-1")
        assert raw_payload not in str(raised.value)

    anyio.run(get_enrichment)
    assert executed_queries[0].parameters == {
        "publication_id": "publication-1",
        "enrichment_id": "enrichment-1",
        "limit": 2,
    }


def test_list_alignments_preserves_distinct_source_target_contexts_for_one_decision() -> None:
    # Given
    executed_queries: list[Neo4jReadQuery] = []
    repository = Neo4jCorpusRepository(
        client=_RoutingReadClient(
            results={
                GET_PUBLICATION_BY_ID_QUERY_ID: Neo4jQueryResult(
                    fields=PUBLICATION_FIELDS,
                    rows=(("publication-node-1", "publication-1", 1, "READY"),),
                    query_type="r",
                ),
                LIST_ALIGNMENTS_QUERY_ID: Neo4jQueryResult(
                    fields=(
                        "source_enrichment_id",
                        "source_posting_id",
                        "source_current",
                        *ALIGNMENT_FIELDS,
                    ),
                    rows=(
                        (
                            "enrichment-node-1",
                            "posting-1",
                            True,
                            False,
                            42,
                            "publication-1",
                            "competency-node-1",
                            "NCS-001",
                            "ncsCompetency",
                            "First competency",
                            "record-1",
                            "run-1",
                        ),
                        (
                            "enrichment-node-2",
                            "posting-2",
                            False,
                            False,
                            42,
                            "publication-1",
                            "competency-node-2",
                            "NCS-002",
                            "ncsCompetency",
                            "Second competency",
                            "record-2",
                            "run-2",
                        ),
                    ),
                    query_type="r",
                ),
            },
            queries=executed_queries,
        ),
        max_rows=10,
    )

    async def list_alignments() -> tuple[tuple[str, str, bool, str], ...]:
        alignments = await repository.list_alignments(
            "publication-1", page=Neo4jPagination(limit=10, offset=0)
        )
        return tuple(
            (
                alignment.source_enrichment.id,
                alignment.source_enrichment.posting_id,
                alignment.source_enrichment.current,
                alignment.competency.id,
            )
            for alignment in alignments
        )

    # When
    relation_contexts = anyio.run(list_alignments)

    # Then
    assert relation_contexts == (
        ("enrichment-node-1", "posting-1", True, "competency-node-1"),
        ("enrichment-node-2", "posting-2", False, "competency-node-2"),
    )
    assert tuple(query.query_id for query in executed_queries) == (
        GET_PUBLICATION_BY_ID_QUERY_ID,
        LIST_ALIGNMENTS_QUERY_ID,
    )
    assert executed_queries[0].parameters == {"publication_id": "publication-1", "limit": 2}


def test_list_alignments_rejects_an_absent_parent_publication_instead_of_an_empty_page() -> None:
    # Given
    repository = Neo4jCorpusRepository(
        client=_RoutingReadClient(
            results={
                GET_PUBLICATION_BY_ID_QUERY_ID: Neo4jQueryResult(
                    fields=PUBLICATION_FIELDS,
                    rows=(),
                    query_type="r",
                ),
                LIST_ALIGNMENTS_QUERY_ID: Neo4jQueryResult(
                    fields=ALIGNMENT_FIELDS,
                    rows=(),
                    query_type="r",
                ),
            },
            queries=[],
        ),
        max_rows=10,
    )

    async def list_alignments() -> None:
        # When / Then
        with pytest.raises(Neo4jPublicationNotFoundError):
            _ = await repository.list_alignments(
                "publication-without-parent", page=Neo4jPagination(limit=10, offset=0)
            )

    anyio.run(list_alignments)

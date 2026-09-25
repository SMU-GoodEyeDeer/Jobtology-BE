from collections.abc import Mapping
from hashlib import sha256

import anyio

from jobtology_be.corpus.neo4j_client import (
    Neo4jQueryResult,
    Neo4jReadQuery,
    Neo4jReadQueryId,
)
from jobtology_be.corpus.neo4j_models import (
    Neo4jNcsAlignment,
    Neo4jNcsCompetencyNode,
    Neo4jOccupationNode,
    Neo4jPublication,
)
from jobtology_be.corpus.neo4j_queries import (
    ALIGNMENT_RESULT_FIELDS,
    ENRICHMENT_FIELDS,
    GET_ENRICHMENT_QUERY_ID,
    GET_PUBLICATION_BY_ID_QUERY_ID,
    LIST_ALIGNMENTS_QUERY_ID,
    LIST_OCCUPATIONS_QUERY_ID,
    LIST_PUBLICATIONS_QUERY_ID,
    OCCUPATION_FIELDS,
    PUBLICATION_FIELDS,
)
from jobtology_be.corpus.neo4j_repository import (
    Neo4jCorpusRepository,
    Neo4jNcsAlignmentSource,
    Neo4jNcsAlignmentWithCompetency,
    Neo4jPagination,
)


class _StaticReadClient:
    _result: Neo4jQueryResult
    _queries: list[Neo4jReadQuery]
    _overrides: Mapping[Neo4jReadQueryId, Neo4jQueryResult]

    def __init__(
        self,
        result: Neo4jQueryResult,
        queries: list[Neo4jReadQuery],
        *,
        overrides: Mapping[Neo4jReadQueryId, Neo4jQueryResult] | None = None,
    ) -> None:
        self._result = result
        self._queries = queries
        self._overrides = {} if overrides is None else overrides

    async def execute(self, query: Neo4jReadQuery) -> Neo4jQueryResult:
        self._queries.append(query)
        return self._overrides.get(query.query_id, self._result)


def test_list_occupations_parses_observed_rows_and_binds_only_pagination() -> None:
    # Given
    executed_queries: list[Neo4jReadQuery] = []
    client = _StaticReadClient(
        result=Neo4jQueryResult(
            fields=OCCUPATION_FIELDS,
            rows=(
                (
                    "occupation-node-1",
                    "NCS-001",
                    "occupation",
                    "Source label",
                    "source-record-1",
                    "source-run-1",
                ),
            ),
            query_type="r",
        ),
        queries=executed_queries,
    )
    repository = Neo4jCorpusRepository(client=client, max_rows=50)
    page = Neo4jPagination(limit=20, offset=10)

    async def list_occupations() -> tuple[Neo4jOccupationNode, ...]:
        return await repository.list_occupations(page=page)

    # When
    occupations = anyio.run(list_occupations)

    # Then
    assert occupations == (
        Neo4jOccupationNode(
            id="occupation-node-1",
            code="NCS-001",
            kind="occupation",
            name="Source label",
            name_source_record_id="source-record-1",
            name_source_run_id="source-run-1",
        ),
    )
    assert len(executed_queries) == 1
    assert executed_queries[0].query_id == LIST_OCCUPATIONS_QUERY_ID
    assert executed_queries[0].parameters == {"limit": 20, "offset": 10}


def test_list_publications_preserves_distinct_source_identifiers_and_state() -> None:
    # Given
    executed_queries: list[Neo4jReadQuery] = []
    client = _StaticReadClient(
        result=Neo4jQueryResult(
            fields=PUBLICATION_FIELDS,
            rows=(("publication-node-1", "publication-1", -1, "READY"),),
            query_type="r",
        ),
        queries=executed_queries,
    )
    repository = Neo4jCorpusRepository(client=client, max_rows=50)

    async def list_publications() -> tuple[Neo4jPublication, ...]:
        return await repository.list_publications(page=Neo4jPagination(limit=20, offset=10))

    # When
    publications = anyio.run(list_publications)

    # Then
    assert publications == (
        Neo4jPublication(
            id="publication-node-1",
            publication_id="publication-1",
            postings=-1,
            state="READY",
        ),
    )
    assert executed_queries[0].query_id == LIST_PUBLICATIONS_QUERY_ID
    assert executed_queries[0].parameters == {"limit": 20, "offset": 10}


def test_list_alignments_parses_safe_source_relation_and_competency_fields() -> None:
    # Given
    executed_queries: list[Neo4jReadQuery] = []
    client = _StaticReadClient(
        result=Neo4jQueryResult(
            fields=ALIGNMENT_RESULT_FIELDS,
            rows=(
                (
                    "enrichment-node-1",
                    "posting-1",
                    True,
                    False,
                    42,
                    "publication-1",
                    "competency-node-1",
                    "NCS-CODE",
                    "ncsCompetency",
                    "Source competency",
                    "source-record-1",
                    "source-run-1",
                ),
            ),
            query_type="r",
        ),
        queries=executed_queries,
        overrides={
            GET_PUBLICATION_BY_ID_QUERY_ID: Neo4jQueryResult(
                fields=PUBLICATION_FIELDS,
                rows=(("publication-node-1", "publication-1", 1, "READY"),),
                query_type="r",
            )
        },
    )
    repository = Neo4jCorpusRepository(client=client, max_rows=50)

    async def list_alignments() -> tuple[Neo4jNcsAlignmentWithCompetency, ...]:
        return await repository.list_alignments(
            "publication-1", page=Neo4jPagination(limit=20, offset=10)
        )

    # When
    alignments = anyio.run(list_alignments)

    # Then
    assert alignments == (
        Neo4jNcsAlignmentWithCompetency(
            source_enrichment=Neo4jNcsAlignmentSource(
                id="enrichment-node-1",
                posting_id="posting-1",
                current=True,
            ),
            alignment=Neo4jNcsAlignment(
                accepted=False,
                decision_id=42,
                publication_id="publication-1",
            ),
            competency=Neo4jNcsCompetencyNode(
                id="competency-node-1",
                code="NCS-CODE",
                kind="ncsCompetency",
                name="Source competency",
                name_source_record_id="source-record-1",
                name_source_run_id="source-run-1",
            ),
        ),
    )
    assert executed_queries[0].query_id == GET_PUBLICATION_BY_ID_QUERY_ID
    assert executed_queries[0].parameters == {"publication_id": "publication-1", "limit": 2}
    assert executed_queries[1].query_id == LIST_ALIGNMENTS_QUERY_ID
    assert executed_queries[1].parameters == {
        "publication_id": "publication-1",
        "limit": 20,
        "offset": 10,
    }


def test_get_enrichment_verifies_the_raw_utf8_payload_hash() -> None:
    # Given
    raw_payload = (
        '{  "item_id":"item-1", "posting_id":"posting-1", "revision_id":"revision-1", '
        '"source_hash":"source-hash", "extraction":{"duties_status":"complete", '
        '"extraction_scope":"scope", "schema_version":"v1"}, "name":"역량" }'
    )
    executed_queries: list[Neo4jReadQuery] = []
    client = _StaticReadClient(
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
            ),
            query_type="r",
        ),
        queries=executed_queries,
    )
    repository = Neo4jCorpusRepository(client=client, max_rows=50)

    async def get_enrichment() -> tuple[str, str, str, bool, str] | None:
        enrichment = await repository.get_enrichment("publication-1", "enrichment-node-1")
        if enrichment is None:
            return None
        return (
            enrichment.id,
            enrichment.publication_id,
            enrichment.posting_id,
            enrichment.current,
            enrichment.payload_hash,
        )

    # When
    enrichment = anyio.run(get_enrichment)

    # Then
    assert enrichment == (
        "enrichment-node-1",
        "publication-1",
        "posting-1",
        True,
        sha256(raw_payload.encode("utf-8")).hexdigest(),
    )
    assert executed_queries[0].query_id == GET_ENRICHMENT_QUERY_ID
    assert executed_queries[0].parameters == {
        "publication_id": "publication-1",
        "enrichment_id": "enrichment-node-1",
        "limit": 2,
    }

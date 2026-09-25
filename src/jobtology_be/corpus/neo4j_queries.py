"""Fixed, observed Neo4j read-query definitions for the native corpus catalog."""

from typing import Final

from jobtology_be.corpus.neo4j_client import (
    Neo4jReadQueryCatalog,
    Neo4jReadQueryDefinition,
    Neo4jReadQueryId,
)

LIST_OCCUPATIONS_QUERY_ID: Final = Neo4jReadQueryId("corpus.occupations.page")
LIST_PUBLICATIONS_QUERY_ID: Final = Neo4jReadQueryId("corpus.publications.page")
GET_PUBLICATION_BY_ID_QUERY_ID: Final = Neo4jReadQueryId("corpus.publication.by-id")
LIST_ALIGNMENTS_QUERY_ID: Final = Neo4jReadQueryId("corpus.publication-alignments.page")
GET_ENRICHMENT_QUERY_ID: Final = Neo4jReadQueryId("corpus.enrichment.by-id")

OCCUPATION_FIELDS: Final = (
    "id",
    "code",
    "kind",
    "name",
    "name_source_record_id",
    "name_source_run_id",
)
PUBLICATION_FIELDS: Final = ("id", "publication_id", "postings", "state")
ALIGNMENT_FIELDS: Final = (
    "accepted",
    "decision_id",
    "publication_id",
    "competency_id",
    "competency_code",
    "competency_kind",
    "competency_name",
    "competency_name_source_record_id",
    "competency_name_source_run_id",
)
ALIGNMENT_RESULT_FIELDS: Final = (
    "source_enrichment_id",
    "source_posting_id",
    "source_current",
    *ALIGNMENT_FIELDS,
)
ENRICHMENT_FIELDS: Final = (
    "id",
    "publication_id",
    "posting_id",
    "current",
    "managed_by",
    "name",
    "payload_json",
    "payload_hash",
)

_OCCUPATIONS_STATEMENT: Final = (
    "MATCH (occupation:occupation) "
    "RETURN occupation.id AS id, occupation.code AS code, occupation.kind AS kind, "
    "occupation.name AS name, occupation.name_source_record_id AS name_source_record_id, "
    "occupation.name_source_run_id AS name_source_run_id "
    "ORDER BY occupation.code, occupation.id SKIP $offset LIMIT $limit"
)
_PUBLICATIONS_STATEMENT: Final = (
    "MATCH (publication:reviewedNcsPublication) "
    "RETURN publication.id AS id, publication.publication_id AS publication_id, "
    "publication.postings AS postings, publication.state AS state "
    "ORDER BY publication.publication_id, publication.id SKIP $offset LIMIT $limit"
)
_PUBLICATION_BY_ID_STATEMENT: Final = (
    "MATCH (publication:reviewedNcsPublication {publication_id: $publication_id}) "
    "RETURN publication.id AS id, publication.publication_id AS publication_id, "
    "publication.postings AS postings, publication.state AS state "
    "ORDER BY publication.id LIMIT $limit"
)
_ALIGNMENTS_STATEMENT: Final = (
    "MATCH (enrichment:reviewedNcsEnrichment {publication_id: $publication_id})"
    "-[alignment:ALIGNS_WITH_NCS {publication_id: $publication_id}]"
    "->(competency:ncsCompetency) "
    "RETURN enrichment.id AS source_enrichment_id, "
    "enrichment.posting_id AS source_posting_id, enrichment.current AS source_current, "
    "alignment.accepted AS accepted, "
    "alignment.decision_id AS decision_id, "
    "alignment.publication_id AS publication_id, competency.id AS competency_id, "
    "competency.code AS competency_code, competency.kind AS competency_kind, "
    "competency.name AS competency_name, "
    "competency.name_source_record_id AS competency_name_source_record_id, "
    "competency.name_source_run_id AS competency_name_source_run_id "
    "ORDER BY alignment.publication_id, enrichment.id, competency.id, alignment.decision_id "
    "SKIP $offset LIMIT $limit"
)
_ENRICHMENT_STATEMENT: Final = (
    "MATCH (enrichment:reviewedNcsEnrichment {id: $enrichment_id, "
    "publication_id: $publication_id}) "
    "RETURN enrichment.id AS id, enrichment.publication_id AS publication_id, "
    "enrichment.posting_id AS posting_id, enrichment.current AS current, "
    "enrichment.managed_by AS managed_by, enrichment.name AS name, "
    "enrichment.payload_json AS payload_json, enrichment.payload_hash AS payload_hash "
    "LIMIT $limit"
)

NEO4J_CORPUS_READ_QUERY_CATALOG: Final = Neo4jReadQueryCatalog(
    definitions=(
        Neo4jReadQueryDefinition(
            query_id=LIST_OCCUPATIONS_QUERY_ID,
            statement=_OCCUPATIONS_STATEMENT,
        ),
        Neo4jReadQueryDefinition(
            query_id=LIST_PUBLICATIONS_QUERY_ID,
            statement=_PUBLICATIONS_STATEMENT,
        ),
        Neo4jReadQueryDefinition(
            query_id=GET_PUBLICATION_BY_ID_QUERY_ID,
            statement=_PUBLICATION_BY_ID_STATEMENT,
        ),
        Neo4jReadQueryDefinition(
            query_id=LIST_ALIGNMENTS_QUERY_ID,
            statement=_ALIGNMENTS_STATEMENT,
        ),
        Neo4jReadQueryDefinition(
            query_id=GET_ENRICHMENT_QUERY_ID,
            statement=_ENRICHMENT_STATEMENT,
        ),
    )
)

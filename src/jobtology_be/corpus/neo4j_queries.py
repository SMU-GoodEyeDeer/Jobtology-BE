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
    "scheme_id",
    "source_release_id",
    "source_uri",
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
    "MATCH (occupation:entity:occupation) WHERE NOT occupation:ontologyObject "
    "AND (NOT occupation:onetCatalogObject OR occupation.current = true) "
    "RETURN occupation.id AS id, occupation.code AS code, occupation.kind AS kind, "
    "occupation.name AS name, occupation.name_source_record_id AS name_source_record_id, "
    "occupation.name_source_run_id AS name_source_run_id, "
    "CASE WHEN occupation:onetCatalogObject THEN 'ONET' ELSE 'NCS' END AS scheme_id, "
    "CASE WHEN occupation:onetCatalogObject THEN occupation.release_id END AS source_release_id, "
    "CASE WHEN occupation:onetCatalogObject THEN occupation.source_uri END AS source_uri "
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
    "->(competency:entity:ncsCompetency) WHERE NOT competency:ontologyObject "
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

ROLE_EVIDENCE_NCS_QUERY_ID: Final = Neo4jReadQueryId("role-evidence.ncs-occupations")
ROLE_EVIDENCE_ONET_QUERY_ID: Final = Neo4jReadQueryId("role-evidence.onet-occupations")
ROLE_CONTEXT_ONET_QUERY_ID: Final = Neo4jReadQueryId("role-evidence.onet-context")

ROLE_EVIDENCE_NCS_FIELDS: Final = (
    "id",
    "code",
    "name",
    "postings",
    "sample_postings",
    "competencies",
    "onet",
    "qualifications",
    "evidence_sources",
    "excluded_associations",
)
ROLE_EVIDENCE_ONET_FIELDS: Final = ("id", "code", "name", "skills", "technologies")
ROLE_CONTEXT_ONET_FIELDS: Final = ("id", "code", "name")

# Resolve current catalog identities independently of NCS co-matches. Posting
# counts come from the same current-source feed used by the posting detail UI.
_ROLE_CONTEXT_ONET_STATEMENT: Final = (
    "MATCH (x:onetCatalogObject:occupation) WHERE x.code IN $codes AND x.current = true "
    "RETURN x.id AS id, x.code AS code, x.name AS name ORDER BY x.code LIMIT $limit"
)

# Per NCS occupation: inferred posting count, a few sample postings, the latest
# current-version competency units (reviewed posting links first), the O*NET
# occupations that the same postings were matched to, and the Q-Net
# qualifications covering the most of its units with their exam sessions.
_ROLE_EVIDENCE_NCS_STATEMENT: Final = (
    "MATCH (o:entity:occupation) WHERE o.code IN $codes AND NOT o:ontologyObject "
    "CALL (o) { OPTIONAL MATCH (o)<-[r:INFERRED_NCS_OCCUPATION]-(p:jobPosting) "
    "WHERE r.job_run_id = p.source_run_id AND r.ncs_run_id = o.source_run_id "
    "RETURN count(DISTINCT p) AS postings } "
    "CALL (o) { MATCH (o)<-[r:INFERRED_NCS_OCCUPATION]-(p:jobPosting) "
    "WHERE r.job_run_id = p.source_run_id AND r.ncs_run_id = o.source_run_id "
    "WITH p ORDER BY p.source_created_at DESC, p.id LIMIT $posting_limit "
    "RETURN collect({id: p.id, title: p.title, source_id: p.source_id}) AS sample_postings } "
    "CALL (o) { MATCH (c:entity:ncsCompetency)-[:CLASSIFIED_AS]->(o) "
    "WHERE NOT c:ontologyObject AND NOT c.name CONTAINS '(구버전)' "
    "WITH left(c.code, 10) AS base, c ORDER BY c.code DESC "
    "WITH base, collect(c)[0] AS latest "
    "OPTIONAL MATCH (:reviewedNcsEnrichment)-[a:ALIGNS_WITH_NCS]->(t:entity:ncsCompetency) "
    "WHERE a.accepted = true AND left(t.code, 10) = base "
    "WITH latest, count(a) AS links ORDER BY links DESC, latest.code LIMIT $competency_limit "
    "RETURN collect({id: latest.id, code: latest.code, name: latest.name, links: links}) "
    "AS competencies } "
    "CALL (o) { MATCH (o)<-[r:INFERRED_NCS_OCCUPATION]-(p:jobPosting)"
    "-[a:INFERRED_ONET_OCCUPATION]->(x:onetCatalogObject:occupation) "
    "WHERE r.job_run_id = p.source_run_id AND r.ncs_run_id = o.source_run_id "
    "AND a.source_facts_sha256 = p.source_facts_sha256 "
    "AND a.occupation_content_hash = x.content_hash AND x.current = true "
    "WITH x, count(DISTINCT p) AS shared, "
    "collect(DISTINCT coalesce(a.review_status, 'UNREVIEWED')) AS review_statuses, "
    "collect(DISTINCT coalesce(a.stage, 'STAGING')) AS stages, "
    "collect(DISTINCT p.source_run_id)[..$provenance_limit] AS source_run_ids, "
    "collect(DISTINCT a.release_id)[..$provenance_limit] AS catalog_pins "
    "ORDER BY shared DESC, x.code LIMIT $onet_limit "
    "RETURN collect({id: x.id, code: x.code, name: x.name, shared: shared, "
    "review_statuses: review_statuses, stages: stages, source_run_ids: source_run_ids, "
    "catalog_pins: catalog_pins}) AS onet } "
    "CALL (o) { MATCH (c:entity:ncsCompetency)-[:CLASSIFIED_AS]->(o) WHERE NOT c:ontologyObject "
    "MATCH (c)-[:QUALIFICATION_MAPPING]->(q:entity:qualification) "
    "WITH q, count(DISTINCT left(c.code, 10)) AS units "
    "ORDER BY units DESC, q.code LIMIT $qualification_limit "
    "OPTIONAL MATCH (q)-[:HAS_EXAM_SESSION]->(e:entity:examSession) "
    "WITH q, units, e ORDER BY e.code DESC "
    "WITH q, units, count(e) AS sessions, collect(e.name)[0] AS latest_session "
    "ORDER BY units DESC, q.code "
    "RETURN collect({id: q.id, code: q.code, name: q.name, units: units, sessions: sessions, "
    "latest_session: latest_session}) AS qualifications } "
    "CALL (o) { MATCH (o)<-[r:INFERRED_NCS_OCCUPATION]-(p:jobPosting) "
    "WITH p.source_id AS source_id, r.job_run_id AS source_run_id, r.ncs_run_id AS catalog_run_id, "
    "coalesce(r.job_run_id = p.source_run_id, false) AS source_current, "
    "coalesce(r.ncs_run_id = o.source_run_id, false) AS catalog_current, "
    "coalesce(r.review_status, 'UNREVIEWED') AS review_status, "
    "coalesce(r.stage, 'STAGING') AS stage, coalesce(r.accepted, false) AS accepted, "
    "count(DISTINCT p) AS postings "
    "WITH * ORDER BY source_id, source_run_id, catalog_run_id, review_status "
    "RETURN collect({source_id: source_id, source_run_id: source_run_id, catalog_run_id: catalog_run_id, "
    "source_current: source_current, catalog_current: catalog_current, review_status: review_status, "
    "stage: stage, accepted: accepted, postings: postings})[..$provenance_limit] AS evidence_sources } "
    "CALL (o) { OPTIONAL MATCH (o)<-[r:INFERRED_NCS_OCCUPATION]-(p:jobPosting) "
    "WHERE NOT coalesce(r.job_run_id = p.source_run_id AND r.ncs_run_id = o.source_run_id, false) "
    "RETURN count(r) AS excluded_associations } "
    "RETURN o.id AS id, o.code AS code, o.name AS name, postings, sample_postings, "
    "competencies, onet, qualifications, evidence_sources, excluded_associations "
    "ORDER BY o.code LIMIT $limit"
)
# Per O*NET occupation: most important essential skills and hot technologies
# (in-demand first, then those hot across the most occupations).
_ROLE_EVIDENCE_ONET_STATEMENT: Final = (
    "MATCH (x:onetCatalogObject:occupation) WHERE x.id IN $ids AND x.current = true "
    "CALL (x) { MATCH (x)-[r:ONET_ESSENTIAL_SKILL]->(e:onetDetail) "
    "WHERE r.scale_name = 'Importance' "
    "WITH e, r ORDER BY r.data_value DESC, e.element_name LIMIT $skill_limit "
    "RETURN collect({id: e.id, name: e.element_name, importance: r.data_value}) AS skills } "
    "CALL (x) { MATCH (x)-[r:ONET_SOFTWARE_EXAMPLE]->(s:onetDetail) "
    "WHERE r.hot_technology = true "
    "WITH s, r, COUNT { (s)<-[h:ONET_SOFTWARE_EXAMPLE]-() WHERE h.hot_technology = true } "
    "AS spread ORDER BY r.in_demand DESC, spread DESC, s.workplace_example "
    "LIMIT $technology_limit "
    "RETURN collect({id: s.id, name: s.workplace_example, in_demand: r.in_demand}) "
    "AS technologies } "
    "RETURN x.id AS id, x.code AS code, x.name AS name, skills, technologies "
    "ORDER BY x.code LIMIT $limit"
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
        Neo4jReadQueryDefinition(
            query_id=ROLE_EVIDENCE_NCS_QUERY_ID,
            statement=_ROLE_EVIDENCE_NCS_STATEMENT,
        ),
        Neo4jReadQueryDefinition(
            query_id=ROLE_EVIDENCE_ONET_QUERY_ID,
            statement=_ROLE_EVIDENCE_ONET_STATEMENT,
        ),
        Neo4jReadQueryDefinition(
            query_id=ROLE_CONTEXT_ONET_QUERY_ID,
            statement=_ROLE_CONTEXT_ONET_STATEMENT,
        ),
    )
)

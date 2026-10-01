from jobtology_be.corpus.neo4j_queries import (
    GET_ENRICHMENT_QUERY_ID,
    GET_PUBLICATION_BY_ID_QUERY_ID,
    LIST_ALIGNMENTS_QUERY_ID,
    LIST_OCCUPATIONS_QUERY_ID,
    LIST_PUBLICATIONS_QUERY_ID,
    NEO4J_CORPUS_READ_QUERY_CATALOG,
)


def test_catalog_registers_only_fixed_observed_read_definitions() -> None:
    # Given
    definitions = NEO4J_CORPUS_READ_QUERY_CATALOG.definitions

    # When
    query_ids = tuple(definition.query_id for definition in definitions)

    # Then
    assert query_ids == (
        LIST_OCCUPATIONS_QUERY_ID,
        LIST_PUBLICATIONS_QUERY_ID,
        GET_PUBLICATION_BY_ID_QUERY_ID,
        LIST_ALIGNMENTS_QUERY_ID,
        GET_ENRICHMENT_QUERY_ID,
    )
    assert all("\n" not in definition.statement for definition in definitions)
    assert all("RETURN *" not in definition.statement for definition in definitions)
    assert "$limit" in NEO4J_CORPUS_READ_QUERY_CATALOG.resolve(GET_ENRICHMENT_QUERY_ID).statement
    assert "$publication_id" in NEO4J_CORPUS_READ_QUERY_CATALOG.resolve(
        GET_PUBLICATION_BY_ID_QUERY_ID
    ).statement


def test_alignment_definition_projects_safe_source_context_in_full_identity_order() -> None:
    # Given
    statement = NEO4J_CORPUS_READ_QUERY_CATALOG.resolve(LIST_ALIGNMENTS_QUERY_ID).statement
    source_context = (
        "RETURN enrichment.id AS source_enrichment_id, "
        + "enrichment.posting_id AS source_posting_id, "
        + "enrichment.current AS source_current, "
    )
    full_identity_order = (
        "ORDER BY alignment.publication_id, enrichment.id, competency.id, "
        + "alignment.decision_id "
        + "SKIP $offset LIMIT $limit"
    )

    # When / Then
    assert source_context in statement
    assert full_identity_order in statement


def test_occupations_query_requires_source_entity_and_excludes_ontology_object() -> None:
    # Given: ontology occupations share :occupation but source occupations also have :entity.
    statement = NEO4J_CORPUS_READ_QUERY_CATALOG.resolve(LIST_OCCUPATIONS_QUERY_ID).statement

    # When: the registered match and predicate are inspected.
    source_match = statement.split(" RETURN ", maxsplit=1)[0]

    # Then: a mixed-label ontology node cannot enter the source catalog.
    assert source_match == (
        "MATCH (occupation:entity:occupation) WHERE NOT occupation:ontologyObject"
    )


def test_alignments_query_requires_source_competency_and_excludes_ontology_object() -> None:
    # Given: ontology competencies share :ncsCompetency but lack source :entity.
    statement = NEO4J_CORPUS_READ_QUERY_CATALOG.resolve(LIST_ALIGNMENTS_QUERY_ID).statement

    # When: the registered traversal and predicate are inspected.
    source_match = statement.split(" RETURN ", maxsplit=1)[0]

    # Then: ontology competencies cannot be returned through source alignments.
    assert source_match.endswith(
        "->(competency:entity:ncsCompetency) WHERE NOT competency:ontologyObject"
    )

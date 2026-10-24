"""Fixed read queries; model/user text is only ever a bound parameter."""
from jobtology_be.corpus.neo4j_client import Neo4jReadQueryDefinition, Neo4jReadQueryId

SEARCH_ID = Neo4jReadQueryId("chat.catalog.search")
EXPAND_ID = Neo4jReadQueryId("chat.catalog.expand")
NODE_ID = Neo4jReadQueryId("chat.catalog.node")
ALLOWED = (
    "NOT n:ontologyObject AND (n:entity:occupation OR n:entity:ncsCompetency OR "
    "n:entity:qualification OR n:entity:examSession OR n:onetCatalogObject:occupation OR "
    "n:onetDetail OR n:reviewedNcsPublication) "
    "AND (NOT n:onetCatalogObject OR n.current = true)"
)
KIND = (
    "CASE WHEN n:ncsCompetency THEN 'ncs_competency' WHEN n:qualification THEN 'qualification' "
    "WHEN n:examSession THEN 'exam_session' WHEN n:onetCatalogObject THEN 'onet_occupation' "
    "WHEN n:onetDetail AND n.workplace_example IS NOT NULL THEN 'technology' "
    "WHEN n:onetDetail THEN 'onet_skill' WHEN n:reviewedNcsPublication THEN 'publication' "
    "ELSE 'ncs_occupation' END"
)
LABEL = "coalesce(n.name, n.element_name, n.workplace_example, n.title, n.code, n.id)"
NODE = (
    "{id:n.id, kind:" + KIND + ", label:substring(" + LABEL + ",0,180), "
    "detail:substring(coalesce(n.description,n.definition,n.name,''),0,1200), "
    "source_pin:coalesce(n.release_id,n.source_run_id,n.publication_id), "
    "occupation_code:CASE WHEN n:occupation THEN n.code ELSE null END, "
    "qualification_code:CASE WHEN n:qualification THEN n.code ELSE null END, "
    "ncs_base_code:CASE WHEN n:ncsCompetency THEN left(n.code,10) ELSE null END, "
    "basis:'REFERENCE'}"
)
RELATIONS = ["CLASSIFIED_AS", "QUALIFICATION_MAPPING", "HAS_EXAM_SESSION", "ONET_ESSENTIAL_SKILL",
             "ONET_SOFTWARE_EXAMPLE", "ALIGNS_WITH_NCS"]
WORKSPACE_QUERIES = (
    Neo4jReadQueryDefinition(query_id=NODE_ID, statement=(
        "MATCH (n {id:$node_id}) WHERE " + ALLOWED + " RETURN " + NODE + " AS node LIMIT $limit"
    )),
    Neo4jReadQueryDefinition(query_id=SEARCH_ID, statement=(
        "MATCH (n) WHERE " + ALLOWED + " AND n.id IS NOT NULL "
        "AND any(term IN $terms WHERE toLower(" + LABEL + ") CONTAINS term "
        "OR toLower(coalesce(n.code,'')) CONTAINS term) "
        "WITH n, size([term IN $terms WHERE toLower(" + LABEL + ") CONTAINS term]) AS score "
        "ORDER BY score DESC, " + LABEL + ", n.id SKIP $offset LIMIT $limit RETURN " + NODE + " AS node"
    )),
    Neo4jReadQueryDefinition(query_id=EXPAND_ID, statement=(
        "MATCH (a {id:$node_id})-[r]-(n) WHERE " + ALLOWED + " AND (" + ALLOWED.replace("n.", "a.").replace("n:", "a:") + ")" +
        " AND n.id IS NOT NULL AND type(r) IN " + repr(RELATIONS) +
        " AND (type(r) <> 'ALIGNS_WITH_NCS' OR r.accepted = true) "
        "WITH a,r,n ORDER BY type(r), n.id SKIP $offset LIMIT $limit "
        "RETURN " + NODE + " AS node, startNode(r).id AS source, endNode(r).id AS target, "
        "type(r) AS kind"
    )),
)

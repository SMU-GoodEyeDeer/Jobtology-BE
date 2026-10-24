"""Optional real-query checks against an isolated, explicitly configured local Neo4j."""
import os
from uuid import uuid4

import anyio
import httpx
import pytest

from jobtology_be.chat.catalog import SharedCatalogRetriever
from jobtology_be.chat.catalog_queries import PARENTS_ID, WORKSPACE_QUERIES
from jobtology_be.corpus.neo4j_client import Neo4jQueryResult, Neo4jReadQuery

URL = os.environ.get("JOBTOLOGY_CHAT_NEO4J_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="isolated Neo4j not configured")


class LocalClient:
    def __init__(self, http):
        self.http = http

    async def execute(self, query):
        definition = next(item for item in WORKSPACE_QUERIES if item.query_id == query.query_id)
        response = await self.http.post(URL, json={"statement": definition.statement,
            "parameters": dict(query.parameters), "accessMode": "Read"})
        body = response.json()
        assert response.status_code == 202 and not body.get("errors"), body.get("errors")
        return Neo4jQueryResult(fields=tuple(body["data"]["fields"]),
                                rows=tuple(tuple(row) for row in body["data"]["values"]), query_type=None)


def test_full_catalog_search_expansion_pagination_and_private_label_exclusion():
    assert URL.startswith("http://127.0.0.1:"), "Only disposable local fixtures may be seeded"
    async def run():
        key = "chatqa" + uuid4().hex
        async with httpx.AsyncClient(timeout=10) as http:
            async def write(statement):
                response = await http.post(URL, json={"statement": statement, "parameters": {"key": key}})
                assert response.status_code == 202 and not response.json().get("errors"), response.json().get("errors")
            await write("CREATE (o:entity:occupation {id:$key+'occ',code:'20010202',name:$key+' 직무'}) "
                        "CREATE (c:entity:ncsCompetency {id:$key+'unit',code:'2001020211_20v1',name:$key+' SQL 역량'}) "
                        "CREATE (q:entity:qualification {id:$key+'qual',code:'C290',name:$key+' 자격'}) "
                        "CREATE (e:entity:examSession {id:$key+'exam',name:$key+' 시험'}) "
                        "CREATE (x:onetCatalogObject:occupation {id:$key+'onet',code:'15-1252.00',name:$key+' Developer',current:true}) "
                        "CREATE (s:onetDetail {id:$key+'skill',element_name:$key+' Critical Thinking'}) "
                        "CREATE (t:onetDetail {id:$key+'tech',workplace_example:$key+' SQL'}) "
                        "CREATE (p:account {id:$key+'private',name:$key+' private'}) "
                        "CREATE (old:onetCatalogObject:occupation {id:$key+'old',name:$key+' old',current:false}) "
                        "CREATE (pub:reviewedNcsPublication {id:$key+'pub',name:$key+' publication',publication_id:'r1'}) "
                        "CREATE (c)-[:CLASSIFIED_AS]->(o), (c)-[:QUALIFICATION_MAPPING]->(q), "
                        "(q)-[:HAS_EXAM_SESSION]->(e), (x)-[:ONET_ESSENTIAL_SKILL]->(s), "
                        "(x)-[:ONET_SOFTWARE_EXAMPLE]->(t), (p)-[:CLASSIFIED_AS]->(c)")
            try:
                catalog = SharedCatalogRetriever(LocalClient(http))
                result = await catalog.search(key)
                ids = {n.id for n in result.nodes}
                assert key + "private" not in ids and key + "old" not in ids
                assert {n.kind for n in result.nodes} >= {"ncs_competency", "ncs_occupation", "qualification", "exam_session", "onet_occupation", "onet_skill", "technology", "publication"}
                expanded = await catalog.expand(key + "unit")
                assert any(e.kind == "CLASSIFIED_AS" for e in expanded.edges)
                assert any(e.kind == "QUALIFICATION_MAPPING" for e in expanded.edges)
                private = await catalog.expand(key + "private")
                assert not private.nodes and not private.edges
                await write("UNWIND range(0,35) AS i CREATE (:onetDetail {id:$key+'page'+toString(i), workplace_example:$key+' page '+toString(i)})")
                first = await catalog.search(key + " page")
                second = await catalog.search(key + " page", 24)
                assert first.truncated and len(first.nodes) == 24
                assert len(second.nodes) == 20  # OR search also finds the 8 original catalog nodes.
                assert not ({n.id for n in first.nodes} & {n.id for n in second.nodes})
            finally:
                await write("MATCH (n) WHERE n.id STARTS WITH $key DETACH DELETE n")
    anyio.run(run)


def test_crosswalk_parents_keep_real_category_edges_and_prefer_visible_onet_occupations():
    assert URL.startswith("http://127.0.0.1:"), "Only disposable local fixtures may be seeded"
    async def run():
        key = "chatqa" + uuid4().hex
        async with httpx.AsyncClient(timeout=10) as http:
            async def write(statement):
                result = await http.post(URL, json={"statement": statement, "parameters": {"key": key}})
                assert result.status_code == 202 and not result.json().get("errors"), result.json().get("errors")
            await write("CREATE (c:entity:ncsCompetency {id:$key+'unit',name:'서버 구현'}) "
                        "CREATE (o:entity:occupation {id:$key+'ncs',name:'응용SW엔지니어링'}) "
                        "CREATE (s:onetDetail {id:$key+'skill',element_name:'Programming'}) "
                        "CREATE (c)-[:CLASSIFIED_AS]->(o) "
                        "WITH s UNWIND range(0,4) AS i "
                        "CREATE (x:onetCatalogObject:occupation {id:$key+'onet'+toString(i),name:'Developer',current:true}) "
                        "CREATE (x)-[:ONET_ESSENTIAL_SKILL]->(s) "
                        "WITH DISTINCT s "
                        "CREATE (old:onetCatalogObject:occupation {id:$key+'old',current:false})-[:ONET_ESSENTIAL_SKILL]->(s) "
                        "CREATE (private:ontologyObject:onetCatalogObject:occupation {id:$key+'private',current:true})-[:ONET_ESSENTIAL_SKILL]->(s)")
            try:
                result = await LocalClient(http).execute(Neo4jReadQuery(query_id=PARENTS_ID,
                    parameters={"ids": [key + "unit", key + "skill"], "context_ids": [key + "onet4"], "limit": 24}))
                assert result.fields == ("node", "source", "target", "kind")
                assert len(result.rows) == 3
                assert {(source, target, kind) for _, source, target, kind in result.rows} == {
                    (key + "unit", key + "ncs", "CLASSIFIED_AS"),
                    (key + "onet4", key + "skill", "ONET_ESSENTIAL_SKILL"),
                    (key + "onet0", key + "skill", "ONET_ESSENTIAL_SKILL"),
                }
                assert {node["kind"] for node, *_ in result.rows} == {"ncs_occupation", "onet_occupation"}
            finally:
                await write("MATCH (n) WHERE n.id STARTS WITH $key DETACH DELETE n")
    anyio.run(run)

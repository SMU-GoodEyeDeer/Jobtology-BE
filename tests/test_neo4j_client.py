import json
from collections.abc import Callable, Coroutine
from typing import Final

import anyio
import httpx
import pytest
from pydantic import SecretStr

from jobtology_be.corpus.neo4j_client import (
    Neo4jQueryApiReadClient,
    Neo4jReadQuery,
    Neo4jReadQueryCatalog,
    Neo4jReadQueryDefinition,
    Neo4jReadQueryId,
    Neo4jUnexpectedStatusError,
    Neo4jUpstreamQueryError,
)
from jobtology_be.corpus.neo4j_client_config import Neo4jQueryApiConfig
from jobtology_be.corpus.neo4j_queries import (
    GET_ENRICHMENT_QUERY_ID,
    NEO4J_CORPUS_READ_QUERY_CATALOG,
)

QUERY_ID: Final = Neo4jReadQueryId("published-release-by-id")
QUERY_STATEMENT: Final = "MATCH (release:PublishedRelease {id: $release_id}) RETURN release.id"


def _config() -> Neo4jQueryApiConfig:
    return Neo4jQueryApiConfig(
        endpoint="https://graph.example.test/db/neo4j/query/v2",
        username="reader",
        password=SecretStr("query-api-password"),
        request_timeout_seconds=3.0,
        transaction_timeout_seconds=4,
        max_rows=2,
        max_request_bytes=1_024,
        max_response_bytes=1_024,
        max_concurrent_requests=1,
    )


def _client(
    handler: Callable[[httpx.Request], Coroutine[None, None, httpx.Response]],
) -> tuple[Neo4jQueryApiReadClient, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        Neo4jQueryApiReadClient(
            config=_config(),
            catalog=Neo4jReadQueryCatalog(
                definitions=(
                    Neo4jReadQueryDefinition(query_id=QUERY_ID, statement=QUERY_STATEMENT),
                )
            ),
            http_client=http_client,
        ),
        http_client,
    )


async def _close(client: Neo4jQueryApiReadClient, http_client: httpx.AsyncClient) -> None:
    await client.aclose()
    await http_client.aclose()


def test_execute_projects_a_successful_bounded_read_query_response() -> None:
    # Given
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://graph.example.test/db/neo4j/query/v2"
        assert request.headers["content-type"] == "application/json"
        assert request.headers["accept"] == "application/json"
        assert request.headers["authorization"].startswith("Basic ")
        assert json.loads(request.content) == {
            "statement": QUERY_STATEMENT,
            "parameters": {"release_id": "release-1"},
            "accessMode": "Read",
            "maxExecutionTime": 4,
        }
        return httpx.Response(
            202,
            json={
                "data": {
                    "fields": ["release.id"],
                    "values": [["release-1"]],
                },
                "queryType": "r",
            },
        )

    client, http_client = _client(handler)

    async def execute() -> None:
        # When
        result = await client.execute(
            Neo4jReadQuery(query_id=QUERY_ID, parameters={"release_id": "release-1"})
        )

        # Then
        assert result.fields == ("release.id",)
        assert result.rows == (("release-1",),)
        assert result.query_type == "r"

    try:
        anyio.run(execute)
    finally:
        anyio.run(_close, client, http_client)


def test_execute_serializes_the_fixed_enrichment_query_with_a_bound_limit() -> None:
    # Given
    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {
            "statement": (
                "MATCH (enrichment:reviewedNcsEnrichment {id: $enrichment_id, "
                "publication_id: $publication_id}) "
                "RETURN enrichment.id AS id, enrichment.publication_id AS publication_id, "
                "enrichment.posting_id AS posting_id, enrichment.current AS current, "
                "enrichment.managed_by AS managed_by, enrichment.name AS name, "
                "enrichment.payload_json AS payload_json, enrichment.payload_hash AS payload_hash "
                "LIMIT $limit"
            ),
            "parameters": {
                "publication_id": "publication-1",
                "enrichment_id": "enrichment-1",
                "limit": 2,
            },
            "accessMode": "Read",
            "maxExecutionTime": 4,
        }
        return httpx.Response(
            202,
            json={"data": {"fields": [], "values": []}, "queryType": "r"},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Neo4jQueryApiReadClient(
        config=_config(),
        catalog=NEO4J_CORPUS_READ_QUERY_CATALOG,
        http_client=http_client,
    )

    async def execute() -> None:
        # When
        result = await client.execute(
            Neo4jReadQuery(
                query_id=GET_ENRICHMENT_QUERY_ID,
                parameters={
                    "publication_id": "publication-1",
                    "enrichment_id": "enrichment-1",
                    "limit": 2,
                },
            )
        )

        # Then
        assert result.fields == ()
        assert result.rows == ()
        assert result.query_type == "r"

    try:
        anyio.run(execute)
    finally:
        anyio.run(_close, client, http_client)


def test_execute_rejects_query_api_errors_in_an_accepted_response_without_leaking_body() -> None:
    # Given
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202,
            json={
                "errors": [
                    {
                        "code": "Neo.ClientError.Statement.SyntaxError",
                        "message": "body-secret must never appear in the raised exception",
                    }
                ]
            },
        )

    client, http_client = _client(handler)

    async def execute() -> None:
        # When / Then
        with pytest.raises(Neo4jUpstreamQueryError) as raised:
            _ = await client.execute(
                Neo4jReadQuery(query_id=QUERY_ID, parameters={"release_id": "release-1"})
            )
        assert raised.value.status_code == 202
        assert raised.value.codes == ("Neo.ClientError.Statement.SyntaxError",)
        assert "body-secret" not in str(raised.value)
        assert "query-api-password" not in str(raised.value)

    try:
        anyio.run(execute)
    finally:
        anyio.run(_close, client, http_client)


def test_execute_rejects_a_non_accepted_status_after_parsing_the_response() -> None:
    # Given
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "data": {
                    "fields": ["release.id"],
                    "values": [["release-1"]],
                }
            },
        )

    client, http_client = _client(handler)

    async def execute() -> None:
        # When / Then
        with pytest.raises(Neo4jUnexpectedStatusError) as raised:
            _ = await client.execute(
                Neo4jReadQuery(query_id=QUERY_ID, parameters={"release_id": "release-1"})
            )
        assert raised.value.status_code == 401

    try:
        anyio.run(execute)
    finally:
        anyio.run(_close, client, http_client)

from collections.abc import Callable, Coroutine
from typing import Final

import anyio
import httpx
import pytest
from pydantic import SecretStr

from jobtology_be.corpus.neo4j_client import (
    Neo4jClientClosedError,
    Neo4jQueryApiReadClient,
    Neo4jReadQuery,
    Neo4jReadQueryCatalog,
    Neo4jReadQueryDefinition,
    Neo4jReadQueryId,
    Neo4jRequestPayloadTooLargeError,
    Neo4jResponsePayloadTooLargeError,
    Neo4jTransportError,
    Neo4jUnknownReadQueryError,
)
from jobtology_be.corpus.neo4j_client_config import Neo4jQueryApiConfig

QUERY_ID: Final = Neo4jReadQueryId("published-release-by-id")
QUERY_STATEMENT: Final = "MATCH (release:PublishedRelease {id: $release_id}) RETURN release.id"


def _client(
    handler: Callable[[httpx.Request], Coroutine[None, None, httpx.Response]],
    *,
    max_request_bytes: int = 1_024,
    max_response_bytes: int = 1_024,
) -> tuple[Neo4jQueryApiReadClient, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Neo4jQueryApiReadClient(
        config=Neo4jQueryApiConfig(
            endpoint="https://graph.example.test/db/neo4j/query/v2",
            username="reader",
            password=SecretStr("query-api-password"),
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
        ),
        catalog=Neo4jReadQueryCatalog(
            definitions=(Neo4jReadQueryDefinition(query_id=QUERY_ID, statement=QUERY_STATEMENT),)
        ),
        http_client=http_client,
    )
    return client, http_client


async def _close(client: Neo4jQueryApiReadClient, http_client: httpx.AsyncClient) -> None:
    await client.aclose()
    await http_client.aclose()


def test_execute_rejects_an_oversized_request_before_calling_httpx() -> None:
    # Given
    handler_calls: list[bool] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        handler_calls.append(True)
        return httpx.Response(202)

    client, http_client = _client(handler, max_request_bytes=1)

    async def execute() -> None:
        # When / Then
        with pytest.raises(Neo4jRequestPayloadTooLargeError):
            _ = await client.execute(
                Neo4jReadQuery(query_id=QUERY_ID, parameters={"release_id": "release-1"})
            )
        assert not handler_calls

    try:
        anyio.run(execute)
    finally:
        anyio.run(_close, client, http_client)


def test_execute_rejects_an_unregistered_query_id_before_calling_httpx() -> None:
    # Given
    handler_calls: list[bool] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        handler_calls.append(True)
        return httpx.Response(202)

    client, http_client = _client(handler)

    async def execute() -> None:
        # When / Then
        with pytest.raises(Neo4jUnknownReadQueryError):
            _ = await client.execute(
                Neo4jReadQuery(
                    query_id=Neo4jReadQueryId("caller-provided-query"),
                    parameters={"release_id": "release-1"},
                )
            )
        assert not handler_calls

    try:
        anyio.run(execute)
    finally:
        anyio.run(_close, client, http_client)


def test_execute_rejects_an_oversized_response_before_parsing_it() -> None:
    # Given
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, content=b"{}", headers={"content-length": "2"})

    client, http_client = _client(handler, max_response_bytes=1)

    async def execute() -> None:
        # When / Then
        with pytest.raises(Neo4jResponsePayloadTooLargeError):
            _ = await client.execute(
                Neo4jReadQuery(query_id=QUERY_ID, parameters={"release_id": "release-1"})
            )

    try:
        anyio.run(execute)
    finally:
        anyio.run(_close, client, http_client)


def test_aclose_keeps_an_injected_http_client_caller_owned_and_rejects_new_queries() -> None:
    # Given
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(202)

    client, http_client = _client(handler)

    # When
    anyio.run(client.aclose)

    # Then
    assert not http_client.is_closed

    async def execute() -> None:
        with pytest.raises(Neo4jClientClosedError):
            _ = await client.execute(
                Neo4jReadQuery(query_id=QUERY_ID, parameters={"release_id": "release-1"})
            )

    try:
        anyio.run(execute)
    finally:
        anyio.run(http_client.aclose)


def test_execute_sanitizes_httpx_transport_errors() -> None:
    # Given
    transport_secret = "transport-secret"

    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(transport_secret)

    client, http_client = _client(handler)

    async def execute() -> None:
        # When / Then
        with pytest.raises(Neo4jTransportError) as raised:
            _ = await client.execute(
                Neo4jReadQuery(query_id=QUERY_ID, parameters={"release_id": "release-1"})
            )
        assert transport_secret not in str(raised.value)
        assert "query-api-password" not in str(raised.value)

    try:
        anyio.run(execute)
    finally:
        anyio.run(_close, client, http_client)

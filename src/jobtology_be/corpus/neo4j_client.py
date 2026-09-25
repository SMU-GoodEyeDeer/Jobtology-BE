"""Bounded HTTPS transport for fixed, internal Neo4j read queries."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType, TracebackType
from typing import ClassVar, Literal, Protocol, Self, final

import anyio
import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from jobtology_be.corpus.neo4j_client_config import Neo4jQueryApiConfig


class Neo4jQueryApiError(Exception):
    """Base error whose message never includes request credentials or body content."""


class Neo4jClientClosedError(Neo4jQueryApiError):
    def __init__(self) -> None:
        super().__init__("Neo4j Query API client is closed")


class Neo4jUnknownReadQueryError(Neo4jQueryApiError):
    def __init__(self) -> None:
        super().__init__("Neo4j read query is not registered")


class Neo4jReadQueryDefinitionError(Neo4jQueryApiError):
    def __init__(self) -> None:
        super().__init__("Neo4j read query definition is invalid")


class Neo4jRequestPayloadError(Neo4jQueryApiError):
    def __init__(self) -> None:
        super().__init__("Neo4j read query parameters are invalid")


class Neo4jRequestPayloadTooLargeError(Neo4jQueryApiError):
    def __init__(self) -> None:
        super().__init__("Neo4j read query request exceeds its payload limit")


class Neo4jRequestTimeoutError(Neo4jQueryApiError):
    def __init__(self) -> None:
        super().__init__("Neo4j Query API request timed out")


class Neo4jTransportError(Neo4jQueryApiError):
    def __init__(self) -> None:
        super().__init__("Neo4j Query API transport failed")


class Neo4jResponsePayloadTooLargeError(Neo4jQueryApiError):
    def __init__(self) -> None:
        super().__init__("Neo4j Query API response exceeds its payload limit")


class Neo4jResponseFormatError(Neo4jQueryApiError):
    status_code: int

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("Neo4j Query API response has an invalid format")


class Neo4jUnexpectedStatusError(Neo4jQueryApiError):
    status_code: int

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("Neo4j Query API returned an unexpected HTTP status")


class Neo4jUpstreamQueryError(Neo4jQueryApiError):
    status_code: int
    codes: tuple[str, ...]

    def __init__(self, *, status_code: int, codes: tuple[str, ...]) -> None:
        self.status_code = status_code
        self.codes = codes
        super().__init__("Neo4j Query API reported an upstream query error")


@dataclass(frozen=True, slots=True)
class Neo4jReadQueryId:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise Neo4jReadQueryDefinitionError()


@dataclass(frozen=True, slots=True)
class Neo4jReadQueryDefinition:
    """Fixed internal Cypher statement, never a caller-provided request value."""

    query_id: Neo4jReadQueryId
    statement: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.statement.strip() or "\n" in self.statement or "\r" in self.statement:
            raise Neo4jReadQueryDefinitionError()


@dataclass(frozen=True, slots=True)
class Neo4jReadQueryCatalog:
    definitions: tuple[Neo4jReadQueryDefinition, ...]

    def __post_init__(self) -> None:
        query_ids = tuple(definition.query_id for definition in self.definitions)
        if len(query_ids) != len(frozenset(query_ids)):
            raise Neo4jReadQueryDefinitionError()

    def resolve(self, query_id: Neo4jReadQueryId) -> Neo4jReadQueryDefinition:
        for definition in self.definitions:
            if definition.query_id == query_id:
                return definition
        raise Neo4jUnknownReadQueryError()


@dataclass(frozen=True, slots=True)
class Neo4jReadQuery:
    """Bound parameters for one registered read query; statement text is intentionally absent."""

    query_id: Neo4jReadQueryId
    parameters: Mapping[str, JsonValue] = field(repr=False)

    def __post_init__(self) -> None:
        if any(not name.strip() for name in self.parameters):
            raise Neo4jRequestPayloadError()
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclass(frozen=True, slots=True)
class Neo4jQueryResult:
    fields: tuple[str, ...]
    rows: tuple[tuple[JsonValue, ...], ...]
    query_type: str | None


class Neo4jReadClient(Protocol):
    """Injection seam that accepts only registered IDs and bound JSON parameters."""

    async def execute(self, query: Neo4jReadQuery) -> Neo4jQueryResult: ...


class _QueryApiRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    statement: str
    parameters: Mapping[str, JsonValue]
    access_mode: Literal["Read"] = Field(serialization_alias="accessMode")
    max_execution_time: int = Field(serialization_alias="maxExecutionTime")


class _QueryApiError(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


class _QueryApiData(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    fields: tuple[str, ...]
    values: tuple[tuple[JsonValue, ...], ...]


class _QueryApiResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    data: _QueryApiData | None = None
    errors: tuple[_QueryApiError, ...] = ()
    query_type: str | None = Field(default=None, validation_alias="queryType")


@final
class Neo4jQueryApiReadClient:
    """Async HTTPS Query API adapter for a catalog of fixed, read-only statements."""

    def __init__(
        self,
        *,
        config: Neo4jQueryApiConfig,
        catalog: Neo4jReadQueryCatalog,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._catalog = catalog
        self._http_client = http_client or _create_http_client(config)
        self._owns_http_client = http_client is None
        self._limiter = anyio.CapacityLimiter(config.max_concurrent_requests)
        self._closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_http_client:
            await self._http_client.aclose()

    async def execute(self, query: Neo4jReadQuery) -> Neo4jQueryResult:
        if self._closed:
            raise Neo4jClientClosedError()
        definition = self._catalog.resolve(query.query_id)
        request_content = _encode_request(definition, query, self._config)
        if len(request_content) > self._config.max_request_bytes:
            raise Neo4jRequestPayloadTooLargeError()
        async with self._limiter:
            if self._closed:
                raise Neo4jClientClosedError()
            return await self._send_and_parse(request_content)

    async def _send_and_parse(self, request_content: bytes) -> Neo4jQueryResult:
        try:
            async with self._http_client.stream(
                "POST",
                self._config.endpoint,
                auth=httpx.BasicAuth(
                    self._config.username,
                    self._config.password.get_secret_value(),
                ),
                content=request_content,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=self._config.request_timeout_seconds,
            ) as response:
                response_content = await _read_bounded_response(
                    response=response,
                    max_response_bytes=self._config.max_response_bytes,
                )
        except httpx.TimeoutException:
            raise Neo4jRequestTimeoutError() from None
        except httpx.HTTPError:
            raise Neo4jTransportError() from None
        return _parse_response(response.status_code, response_content, self._config.max_rows)


def _create_http_client(config: Neo4jQueryApiConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        headers={"Accept": "application/json"},
        limits=httpx.Limits(
            max_connections=config.max_concurrent_requests,
            max_keepalive_connections=config.max_concurrent_requests,
        ),
        timeout=httpx.Timeout(config.request_timeout_seconds),
        trust_env=False,
        verify=True,
    )


def _encode_request(
    definition: Neo4jReadQueryDefinition,
    query: Neo4jReadQuery,
    config: Neo4jQueryApiConfig,
) -> bytes:
    try:
        return _QueryApiRequest(
            statement=definition.statement,
            parameters=query.parameters,
            access_mode="Read",
            max_execution_time=config.transaction_timeout_seconds,
        ).model_dump_json(by_alias=True).encode()
    except ValidationError:
        raise Neo4jRequestPayloadError() from None


async def _read_bounded_response(
    *, response: httpx.Response, max_response_bytes: int
) -> bytes:
    response_content = bytearray()
    async for chunk in response.aiter_bytes():
        response_content.extend(chunk)
        if len(response_content) > max_response_bytes:
            raise Neo4jResponsePayloadTooLargeError()
    return bytes(response_content)


def _parse_response(
    status_code: int, response_content: bytes, max_rows: int
) -> Neo4jQueryResult:
    try:
        response = _QueryApiResponse.model_validate_json(response_content)
    except ValidationError:
        raise Neo4jResponseFormatError(status_code) from None
    if response.errors:
        raise Neo4jUpstreamQueryError(
            status_code=status_code,
            codes=tuple(error.code for error in response.errors),
        )
    if status_code != 202:
        raise Neo4jUnexpectedStatusError(status_code)
    if response.data is None:
        raise Neo4jResponseFormatError(status_code)
    if len(response.data.values) > max_rows or any(
        len(row) != len(response.data.fields) for row in response.data.values
    ):
        raise Neo4jResponseFormatError(status_code)
    return Neo4jQueryResult(
        fields=response.data.fields,
        rows=response.data.values,
        query_type=response.query_type,
    )

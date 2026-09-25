"""Configuration for the HTTPS Neo4j Query API adapter."""

from dataclasses import dataclass
from typing import Protocol, Self, runtime_checkable
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from pydantic import SecretStr

_SUPPORTED_SOURCE_SCHEMES = frozenset(
    {
        "bolt",
        "bolt+s",
        "bolt+ssc",
        "neo4j",
        "neo4j+s",
        "neo4j+ssc",
        "https",
    }
)
_SUPPORTED_SOURCE_PROTOCOLS = frozenset(f"{scheme}://" for scheme in _SUPPORTED_SOURCE_SCHEMES)


class Neo4jConfigurationError(Exception):
    def __init__(self) -> None:
        super().__init__("Neo4j Query API configuration is invalid")


class Neo4jCredentialsSource(Protocol):
    """Minimal already-parsed secret settings shape used by the Query API adapter."""

    @property
    def db_link(self) -> SecretStr | None: ...

    @property
    def db_password(self) -> SecretStr | None: ...


@runtime_checkable
class Neo4jConfiguredSource(Neo4jCredentialsSource, Protocol):
    """API and worker settings that can describe either configured source-link form."""

    @property
    def db_protocol(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class Neo4jQueryApiConfig:
    """HTTPS endpoint config; only explicit HTTPS source URIs preserve a nondefault port."""

    endpoint: str
    username: str
    password: SecretStr
    database: str = "neo4j"
    request_timeout_seconds: float = 10.0
    transaction_timeout_seconds: int = 5
    max_rows: int = 100
    max_request_bytes: int = 32_768
    max_response_bytes: int = 1_048_576
    max_concurrent_requests: int = 4

    def __post_init__(self) -> None:
        expected_path = _query_api_path(self.database)
        try:
            parsed = urlsplit(self.endpoint)
        except ValueError:
            raise Neo4jConfigurationError() from None
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != expected_path
            or parsed.query
            or parsed.fragment
            or not self.username.strip()
            or self.request_timeout_seconds <= 0
            or self.transaction_timeout_seconds <= 0
            or self.max_rows <= 0
            or self.max_request_bytes <= 0
            or self.max_response_bytes <= 0
            or self.max_concurrent_requests <= 0
        ):
            raise Neo4jConfigurationError()

    @classmethod
    def from_configured_source(cls, source: Neo4jConfiguredSource) -> Self:
        """Build one HTTPS configuration from API or worker settings without loading env files."""
        source_link = source.db_link
        password = source.db_password
        if source_link is None or password is None:
            raise Neo4jConfigurationError()
        return cls.from_source_uri(
            source_uri=_source_uri(source_link, source.db_protocol), password=password
        )

    @classmethod
    def from_settings(cls, settings: Neo4jCredentialsSource) -> Self:
        if isinstance(settings, Neo4jConfiguredSource):
            return cls.from_configured_source(settings)
        source_link = settings.db_link
        password = settings.db_password
        if source_link is None or password is None:
            raise Neo4jConfigurationError()
        return cls.from_source_uri(source_uri=source_link, password=password)

    @classmethod
    def from_source_uri(
        cls,
        *,
        source_uri: SecretStr,
        password: SecretStr,
        database: str = "neo4j",
    ) -> Self:
        """Convert a configured Neo4j connection URI into the HTTPS Query API endpoint."""
        try:
            parsed = urlsplit(source_uri.get_secret_value())
            source_port = parsed.port
        except ValueError:
            raise Neo4jConfigurationError() from None
        if (
            parsed.scheme not in _SUPPORTED_SOURCE_SCHEMES
            or parsed.hostname is None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise Neo4jConfigurationError()
        username = unquote(parsed.username) if parsed.username is not None else "neo4j"
        host = _format_host(parsed.hostname)
        port = source_port if parsed.scheme == "https" else None
        authority = f"{host}:{port}" if port is not None else host
        endpoint = urlunsplit(("https", authority, _query_api_path(database), "", ""))
        return cls(endpoint=endpoint, username=username, password=password, database=database)


def _query_api_path(database: str) -> str:
    if not database.strip() or "/" in database:
        raise Neo4jConfigurationError()
    return f"/db/{quote(database, safe='')}/query/v2"


def _source_uri(source_link: SecretStr, source_protocol: str | None) -> SecretStr:
    raw_source_link = source_link.get_secret_value()
    if "://" in raw_source_link:
        return source_link
    if source_protocol not in _SUPPORTED_SOURCE_PROTOCOLS:
        raise Neo4jConfigurationError()
    return SecretStr(f"{source_protocol}{raw_source_link}")


def _format_host(hostname: str) -> str:
    if ":" in hostname:
        return f"[{hostname}]"
    return hostname

from dataclasses import dataclass

import pytest
from pydantic import SecretStr

from jobtology_be.corpus.neo4j_client_config import (
    Neo4jConfigurationError,
    Neo4jQueryApiConfig,
)
from jobtology_be.settings import Settings
from jobtology_be.workers.main import WorkerSettings


@dataclass(frozen=True, slots=True)
class _StaticConfiguredSource:
    db_link: SecretStr | None
    db_password: SecretStr | None
    db_protocol: str | None


def test_config_from_settings_converts_a_bolt_source_to_a_credential_free_https_endpoint() -> None:
    # Given
    uri_password = "uri-password"
    query_api_password = "query-api-password"
    settings = Settings.model_construct(
        db_link=SecretStr(f"neo4j://reader:{uri_password}@graph.example.test:7687"),
        db_password=SecretStr(query_api_password),
        db_protocol=None,
    )

    # When
    config = Neo4jQueryApiConfig.from_settings(settings)

    # Then
    assert config.endpoint == "https://graph.example.test/db/neo4j/query/v2"
    assert config.username == "reader"
    assert config.database == "neo4j"
    assert uri_password not in repr(config)
    assert query_api_password not in repr(config)


def test_config_defaults_the_neo4j_user_and_database_when_the_source_omits_them() -> None:
    # Given / When
    config = Neo4jQueryApiConfig.from_source_uri(
        source_uri=SecretStr("bolt+s://graph.example.test:7687"),
        password=SecretStr("query-api-password"),
    )

    # Then
    assert config.endpoint == "https://graph.example.test/db/neo4j/query/v2"
    assert config.username == "neo4j"
    assert config.database == "neo4j"


def test_config_from_configured_source_accepts_worker_settings_without_duplicate_uri_parsing() -> None:
    # Given
    settings = WorkerSettings.model_construct(
        db_link=SecretStr("neo4j://reader:uri-password@graph.example.test:7687"),
        db_password=SecretStr("query-api-password"),
        db_protocol=None,
    )

    # When
    config = Neo4jQueryApiConfig.from_configured_source(settings)

    # Then
    assert config.endpoint == "https://graph.example.test/db/neo4j/query/v2"
    assert config.username == "reader"


def test_config_from_configured_source_uses_https_default_port_for_a_bare_bolt_host() -> None:
    # Given
    settings = WorkerSettings.model_construct(
        db_link=SecretStr("graph.example.test:7687"),
        db_password=SecretStr("query-api-password"),
        db_protocol="bolt+s://",
    )

    # When
    config = Neo4jQueryApiConfig.from_configured_source(settings)
    compatibility_config = Neo4jQueryApiConfig.from_settings(settings)

    # Then
    assert config.endpoint == "https://graph.example.test/db/neo4j/query/v2"
    assert config.username == "neo4j"
    assert compatibility_config == config


def test_config_preserves_an_explicit_https_query_api_port_override() -> None:
    # Given / When
    config = Neo4jQueryApiConfig.from_source_uri(
        source_uri=SecretStr("https://graph.example.test:7473"),
        password=SecretStr("query-api-password"),
    )

    # Then
    assert config.endpoint == "https://graph.example.test:7473/db/neo4j/query/v2"


def test_config_rejects_a_bare_source_without_protocol_without_echoing_secrets() -> None:
    # Given
    source_link = "graph.example.test"
    query_api_password = "query-api-password"
    settings = _StaticConfiguredSource(
        db_link=SecretStr(source_link),
        db_password=SecretStr(query_api_password),
        db_protocol=None,
    )

    # When / Then
    with pytest.raises(Neo4jConfigurationError) as raised:
        _ = Neo4jQueryApiConfig.from_configured_source(settings)
    assert source_link not in str(raised.value)
    assert query_api_password not in str(raised.value)


def test_config_rejects_embedded_endpoint_credentials_without_echoing_them() -> None:
    # Given
    endpoint_secret = "endpoint-password"
    query_api_password = "query-api-password"

    # When / Then
    with pytest.raises(Neo4jConfigurationError) as raised:
        _ = Neo4jQueryApiConfig(
            endpoint=f"https://reader:{endpoint_secret}@graph.example.test/db/neo4j/query/v2",
            username="reader",
            password=SecretStr(query_api_password),
        )
    assert endpoint_secret not in str(raised.value)
    assert query_api_password not in str(raised.value)

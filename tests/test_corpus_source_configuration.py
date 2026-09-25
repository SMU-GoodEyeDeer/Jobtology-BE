from pathlib import Path

import pytest
from pydantic import ValidationError

from jobtology_be.settings import Settings
from jobtology_be.workers.main import WorkerSettings

SYNTHETIC_DATABASE_URL = "postgresql+asyncpg://app@127.0.0.1:5432/jobtology"
SYNTHETIC_NEO4J_LINK = "neo4j+s://reader@graph.example.test"
SYNTHETIC_NEO4J_PASSWORD = "synthetic-query-api-password"
SOURCE_CONFIGURATION_ENVIRONMENT_KEYS = (
    "JOBTOLOGY_ENVIRONMENT",
    "JOBTOLOGY_DATABASE_URL",
    "JOBTOLOGY_CORPUS_SNAPSHOT_PATH",
    "JOBTOLOGY_CORPUS_SOURCE",
    "JOBTOLOGY_DB_LINK",
    "JOBTOLOGY_DB_PASSWORD",
    "JOBTOLOGY_DB_PROTOCOL",
)


def _clear_source_configuration_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in SOURCE_CONFIGURATION_ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_api_settings_accept_explicit_neo4j_query_api_without_local_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    _clear_source_configuration_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    values = {
        "corpus_source": "neo4j_query_api",
        "db_link": SYNTHETIC_NEO4J_LINK,
        "db_password": SYNTHETIC_NEO4J_PASSWORD,
    }

    # When
    settings = Settings.model_validate(values)

    # Then
    assert settings.corpus_source == "neo4j_query_api"
    assert settings.corpus_snapshot_path is None


def test_worker_settings_accept_explicit_neo4j_query_api_without_local_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    _clear_source_configuration_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    values = {
        "database_url": SYNTHETIC_DATABASE_URL,
        "corpus_source": "neo4j_query_api",
        "db_link": SYNTHETIC_NEO4J_LINK,
        "db_password": SYNTHETIC_NEO4J_PASSWORD,
    }

    # When
    settings = WorkerSettings.model_validate(values)

    # Then
    assert settings.corpus_source == "neo4j_query_api"
    assert settings.corpus_snapshot_path is None


def test_settings_reject_neo4j_query_api_without_complete_connection_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    _clear_source_configuration_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    values = {"corpus_source": "neo4j_query_api", "db_link": SYNTHETIC_NEO4J_LINK}

    # When / Then
    with pytest.raises(ValidationError):
        _ = Settings.model_validate(values)


def test_worker_settings_reject_default_local_source_without_snapshot_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given
    _clear_source_configuration_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    values = {"database_url": SYNTHETIC_DATABASE_URL}

    # When / Then
    with pytest.raises(ValidationError):
        _ = WorkerSettings.model_validate(values)

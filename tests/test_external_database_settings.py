from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from jobtology_be.settings import Settings
from jobtology_be.workers.main import WorkerSettings

SYNTHETIC_DB_LINK = "neo4j://synthetic-user:synthetic-link-password@graph.example.test:7687"
SYNTHETIC_DB_PASSWORD = "synthetic-db-password"
SYNTHETIC_DB_PROTOCOL = "bolt+s://"
SETTINGS_ENVIRONMENT_VARIABLES = (
    "JOBTOLOGY_ENVIRONMENT",
    "JOBTOLOGY_DATABASE_URL",
    "JOBTOLOGY_CORPUS_SNAPSHOT_PATH",
    "JOBTOLOGY_DB_LINK",
    "JOBTOLOGY_DB_PASSWORD",
    "JOBTOLOGY_DB_PROTOCOL",
    "JOBTOLOGY_ENABLE_FIXTURES",
    "JOBTOLOGY_ENABLE_FE_MOCK_SAMPLES",
    "JOBTOLOGY_CORS_ORIGINS",
    "JOBTOLOGY_AUTH_ENABLED",
    "JOBTOLOGY_GOOGLE_CLIENT_ID",
    "JOBTOLOGY_GOOGLE_CLIENT_SECRET",
    "JOBTOLOGY_GOOGLE_REDIRECT_URI",
    "JOBTOLOGY_FRONTEND_URL",
    "JOBTOLOGY_SESSION_TTL_SECONDS",
    "JOBTOLOGY_WORKER_BATCH_LIMIT",
)


@pytest.fixture
def isolated_settings_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for variable in SETTINGS_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_settings_parse_external_database_metadata_without_an_application_database(
    isolated_settings_directory: Path,
) -> None:
    # Given
    settings_file = isolated_settings_directory / ".env"
    _ = settings_file.write_text(
        "\n".join(
            (
                f"JOBTOLOGY_DB_LINK={SYNTHETIC_DB_LINK}",
                f"JOBTOLOGY_DB_PASSWORD={SYNTHETIC_DB_PASSWORD}",
                f"JOBTOLOGY_DB_PROTOCOL={SYNTHETIC_DB_PROTOCOL}",
            )
        )
    )

    # When
    settings = Settings()

    # Then
    assert settings.database_url is None
    assert isinstance(settings.db_link, SecretStr)
    assert isinstance(settings.db_password, SecretStr)
    assert settings.db_link.get_secret_value() == SYNTHETIC_DB_LINK
    assert settings.db_password.get_secret_value() == SYNTHETIC_DB_PASSWORD
    assert settings.db_protocol == SYNTHETIC_DB_PROTOCOL
    assert SYNTHETIC_DB_LINK not in repr(settings)
    assert SYNTHETIC_DB_PASSWORD not in repr(settings)


def test_settings_reject_invalid_external_database_protocol_without_secret_input(
    isolated_settings_directory: Path,
) -> None:
    # Given
    settings_file = isolated_settings_directory / ".env"
    _ = settings_file.write_text(
        "\n".join(
            (
                f"JOBTOLOGY_DB_LINK={SYNTHETIC_DB_LINK}",
                f"JOBTOLOGY_DB_PASSWORD={SYNTHETIC_DB_PASSWORD}",
                "JOBTOLOGY_DB_PROTOCOL=unsupported://",
            )
        )
    )

    # When
    with pytest.raises(ValidationError) as raised:
        _ = Settings()

    # Then
    assert any(
        error["loc"] == ("db_protocol",) and error["type"] == "literal_error"
        for error in raised.value.errors()
    )
    assert SYNTHETIC_DB_LINK not in str(raised.value)
    assert SYNTHETIC_DB_PASSWORD not in str(raised.value)


def test_settings_reject_unknown_dotenv_key_without_external_database_secret_input(
    isolated_settings_directory: Path,
) -> None:
    # Given
    settings_file = isolated_settings_directory / ".env"
    _ = settings_file.write_text(
        "\n".join(
            (
                f"JOBTOLOGY_DB_LINK={SYNTHETIC_DB_LINK}",
                f"JOBTOLOGY_DB_PASSWORD={SYNTHETIC_DB_PASSWORD}",
                f"JOBTOLOGY_DB_PROTOCOL={SYNTHETIC_DB_PROTOCOL}",
                "JOBTOLOGY_DB_PROTCOL=typo",
            )
        )
    )

    # When
    with pytest.raises(ValidationError) as raised:
        _ = Settings()

    # Then
    assert any(error["type"] == "extra_forbidden" for error in raised.value.errors())
    assert SYNTHETIC_DB_LINK not in str(raised.value)
    assert SYNTHETIC_DB_PASSWORD not in str(raised.value)


def test_worker_settings_parse_external_database_metadata(
    isolated_settings_directory: Path,
) -> None:
    # Given
    snapshot_path = isolated_settings_directory / "published-corpus.json"
    settings_file = isolated_settings_directory / ".env"
    _ = settings_file.write_text(
        "\n".join(
            (
                "JOBTOLOGY_DATABASE_URL=postgresql+asyncpg://app@127.0.0.1:5432/jobtology",
                f"JOBTOLOGY_CORPUS_SNAPSHOT_PATH={snapshot_path}",
                f"JOBTOLOGY_DB_LINK={SYNTHETIC_DB_LINK}",
                f"JOBTOLOGY_DB_PASSWORD={SYNTHETIC_DB_PASSWORD}",
                f"JOBTOLOGY_DB_PROTOCOL={SYNTHETIC_DB_PROTOCOL}",
            )
        )
    )

    # When
    settings = WorkerSettings.model_validate({})

    # Then
    assert settings.database_url == "postgresql+asyncpg://app@127.0.0.1:5432/jobtology"
    assert settings.corpus_snapshot_path == snapshot_path
    assert isinstance(settings.db_link, SecretStr)
    assert isinstance(settings.db_password, SecretStr)
    assert settings.db_link.get_secret_value() == SYNTHETIC_DB_LINK
    assert settings.db_password.get_secret_value() == SYNTHETIC_DB_PASSWORD
    assert settings.db_protocol == SYNTHETIC_DB_PROTOCOL
    assert SYNTHETIC_DB_LINK not in repr(settings)
    assert SYNTHETIC_DB_PASSWORD not in repr(settings)


def test_settings_validation_hides_synthetic_external_database_inputs() -> None:
    # Given
    invalid_settings = {
        "environment": "production",
        "enable_fixtures": True,
        "database_url": "postgresql+asyncpg://app@127.0.0.1:5432/jobtology",
        "db_link": SYNTHETIC_DB_LINK,
        "db_password": SYNTHETIC_DB_PASSWORD,
    }

    # When
    with pytest.raises(ValidationError) as raised:
        _ = Settings.model_validate(invalid_settings)

    # Then
    assert SYNTHETIC_DB_LINK not in str(raised.value)
    assert SYNTHETIC_DB_PASSWORD not in str(raised.value)


def test_worker_settings_validation_hides_synthetic_external_database_inputs() -> None:
    # Given
    invalid_settings = {
        "db_link": SYNTHETIC_DB_LINK,
        "db_password": SYNTHETIC_DB_PASSWORD,
    }

    # When
    with pytest.raises(ValidationError) as raised:
        _ = WorkerSettings.model_validate(invalid_settings)

    # Then
    assert SYNTHETIC_DB_LINK not in str(raised.value)
    assert SYNTHETIC_DB_PASSWORD not in str(raised.value)

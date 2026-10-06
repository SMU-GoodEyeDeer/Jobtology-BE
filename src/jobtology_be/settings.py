from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import ClassVar, Literal, assert_never
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

type ExternalDatabaseProtocol = Literal[
    "bolt://",
    "bolt+ssc://",
    "bolt+s://",
    "neo4j://",
    "neo4j+ssc://",
    "neo4j+s://",
]
type CorpusSource = Literal["local_json", "neo4j_query_api"]


@dataclass(frozen=True, slots=True)
class GoogleOidcSettings:
    client_id: str | None
    client_secret: SecretStr | None
    redirect_uri: str | None
    frontend_url: str | None

    @property
    def is_configured(self) -> bool:
        return (
            self.client_id is not None
            and bool(self.client_id.strip())
            and self.client_secret is not None
            and bool(self.client_secret.get_secret_value().strip())
            and self.redirect_uri is not None
            and bool(self.redirect_uri.strip())
            and self.frontend_url is not None
            and bool(self.frontend_url.strip())
        )


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="JOBTOLOGY_", env_file=".env", extra="forbid", hide_input_in_errors=True
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str | None = None
    catalog_database_url: SecretStr | None = None
    editorial_draft_path: Path | None = None
    corpus_snapshot_path: Path | None = None
    corpus_source: CorpusSource = "local_json"
    db_link: SecretStr | None = None
    db_password: SecretStr | None = None
    db_protocol: ExternalDatabaseProtocol | None = None
    enable_fixtures: bool = False
    enable_fe_mock_samples: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    auth_enabled: bool = False
    guest_sessions_enabled: bool = False
    product_roles_enabled: bool = False
    product_role_artifact_approval_path: Path | None = None
    inprocess_worker_enabled: bool = False
    capability_list_authoritative: bool = False
    solver_worker_count: int = Field(default=8, ge=1, le=16)
    solver_time_limit_seconds: float | None = Field(default=10.0, gt=0, le=60)
    llm_provider: Literal["disabled", "openai_api_key", "chatgpt_oauth"] = "disabled"
    openai_api_key: SecretStr | None = None
    openai_model: str = Field(default="gpt-5.6-luna", min_length=1)
    openai_oauth_store: Literal["database", "file"] = "database"
    openai_oauth_auth_path: Path | None = None
    openai_oauth_refresh: bool = False
    credential_encryption_key: SecretStr | None = None
    guest_session_max_new_per_minute: int = Field(default=30, ge=1, le=1000)
    google_client_id: str | None = Field(default=None, min_length=1)
    google_client_secret: SecretStr | None = Field(default=None, min_length=1)
    google_redirect_uri: str | None = Field(default=None, min_length=1)
    frontend_url: str | None = Field(default=None, min_length=1)
    session_ttl_seconds: int = Field(default=60 * 60 * 24 * 7, gt=0)

    @property
    def google_oidc_settings(self) -> GoogleOidcSettings:
        return GoogleOidcSettings(
            client_id=self.google_client_id,
            client_secret=self.google_client_secret,
            redirect_uri=self.google_redirect_uri,
            frontend_url=self.frontend_url,
        )

    @property
    def session_lifetime(self) -> timedelta:
        return timedelta(seconds=self.session_ttl_seconds)

    @property
    def session_cookie_secure(self) -> bool:
        match self.environment:
            case "production":
                return True
            case "test":
                return False
            case "development":
                return (
                    not self.guest_sessions_enabled
                    and self.google_redirect_uri is not None
                    and not _is_insecure_localhost_url(self.google_redirect_uri)
                )
            case unreachable:
                assert_never(unreachable)

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.catalog_database_url is not None:
            try:
                catalog_url = make_url(self.catalog_database_url.get_secret_value())
            except ArgumentError:
                raise ValueError("Catalog connection must use the dedicated async reader") from None
            if (
                catalog_url.drivername != "postgresql+asyncpg"
                or catalog_url.username != "jobtology_catalog_reader"
                or not catalog_url.database
            ):
                raise ValueError("Catalog connection must use the dedicated async reader")
        match self.environment:
            case "production":
                if self.enable_fixtures or self.enable_fe_mock_samples:
                    raise ValueError("Fixtures cannot be enabled in production")
                if self.database_url is None:
                    raise ValueError("A database URL is required in production")
            case "development" | "test":
                pass
            case unreachable:
                assert_never(unreachable)
        match self.corpus_source:
            case "local_json":
                pass
            case "neo4j_query_api":
                if self.db_link is None or self.db_password is None:
                    raise ValueError(
                        "Neo4j Query API source requires database link and password metadata"
                    )
            case unreachable:
                assert_never(unreachable)
        _validate_cors_origins(self.cors_origins)
        google_values = (
            self.google_client_id,
            self.google_client_secret,
            self.google_redirect_uri,
        )
        provided_google_values = sum(value is not None for value in google_values)
        if self.auth_enabled and self.guest_sessions_enabled:
            raise ValueError("Google authentication and guest sessions cannot both be enabled")
        if not self.guest_sessions_enabled and 0 < provided_google_values < len(google_values):
            raise ValueError(
                "Google OIDC configuration must provide client ID, client secret, and redirect URI together"
            )
        if self.frontend_url is not None and not self.guest_sessions_enabled:
            _validate_origin(self.frontend_url, "Frontend URL")
        if self.auth_enabled:
            if (
                self.google_client_id is None
                or self.google_client_secret is None
                or self.google_redirect_uri is None
                or self.frontend_url is None
            ):
                raise ValueError(
                    "Authentication requires Google client ID, client secret, redirect URI, and frontend URL"
                )
            if not self.google_oidc_settings.is_configured:
                raise ValueError("Authentication requires nonblank Google OIDC configuration")
            _validate_redirect_uri(self.google_redirect_uri)
            if self.frontend_url not in self.cors_origins:
                raise ValueError("The frontend URL must be an explicit CORS origin")
            match self.environment:
                case "production":
                    if not _is_https_url(self.google_redirect_uri) or not _is_https_url(
                        self.frontend_url
                    ):
                        raise ValueError("Production authentication URLs must use HTTPS")
                case "development":
                    if not _is_permitted_development_url(self.google_redirect_uri) or not _is_permitted_development_url(
                        self.frontend_url
                    ):
                        raise ValueError("Insecure development authentication URLs must use localhost")
                case "test":
                    pass
                case unreachable:
                    assert_never(unreachable)
        return self


def _validate_cors_origins(origins: list[str]) -> None:
    for origin in origins:
        if origin == "*":
            raise ValueError("Credentialed CORS cannot use a wildcard origin")
        _validate_origin(origin, "CORS origin")


def _validate_origin(value: str, label: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ""
        or parsed.query != ""
        or parsed.fragment != ""
    ):
        raise ValueError(f"{label} must be an exact HTTP(S) origin")


def _validate_redirect_uri(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/api/v1/auth/google/callback"
        or parsed.query != ""
        or parsed.fragment != ""
    ):
        raise ValueError("Google redirect URI must target the exact backend callback URL")


def _is_https_url(value: str) -> bool:
    return urlsplit(value).scheme == "https"


def _is_insecure_localhost_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "http" and parsed.hostname == "localhost"


def _is_permitted_development_url(value: str) -> bool:
    return _is_https_url(value) or _is_insecure_localhost_url(value)

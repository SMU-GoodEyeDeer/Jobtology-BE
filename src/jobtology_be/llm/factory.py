from typing import assert_never

from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.llm.client import ChatGptOAuthClient, LlmClient, OpenAIApiKeyClient
from jobtology_be.llm.credentials import (
    CredentialStore,
    DatabaseCredentialStore,
    FileCredentialStore,
    parse_encryption_key,
)
from jobtology_be.settings import Settings


def build_credential_store(settings: Settings, database: Database | None) -> CredentialStore | None:
    match settings.openai_oauth_store:
        case "file":
            if settings.openai_oauth_auth_path is None:
                return None
            return FileCredentialStore(settings.openai_oauth_auth_path)
        case "database":
            if database is None or settings.credential_encryption_key is None:
                return None
            return DatabaseCredentialStore(
                database, parse_encryption_key(settings.credential_encryption_key.get_secret_value())
            )
        case unreachable:
            assert_never(unreachable)


def build_llm_client(settings: Settings, database: Database | None) -> LlmClient | None:
    match settings.llm_provider:
        case "disabled":
            return None
        case "openai_api_key":
            if settings.openai_api_key is None:
                return None
            return OpenAIApiKeyClient(
                api_key=settings.openai_api_key.get_secret_value(), model=settings.openai_model
            )
        case "chatgpt_oauth":
            store = build_credential_store(settings, database)
            if store is None:
                return None
            return ChatGptOAuthClient(
                store=store, model=settings.openai_model, refresh_enabled=settings.openai_oauth_refresh
            )
        case unreachable:
            assert_never(unreachable)

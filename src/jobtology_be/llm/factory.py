from typing import assert_never

from jobtology_be.llm.client import ChatGptOAuthClient, LlmClient, OpenAIApiKeyClient
from jobtology_be.settings import Settings


def build_llm_client(settings: Settings) -> LlmClient | None:
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
            if settings.openai_oauth_auth_path is None:
                return None
            return ChatGptOAuthClient(
                auth_path=settings.openai_oauth_auth_path,
                model=settings.openai_model,
                refresh_enabled=settings.openai_oauth_refresh,
            )
        case unreachable:
            assert_never(unreachable)

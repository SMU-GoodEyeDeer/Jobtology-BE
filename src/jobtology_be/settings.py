from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JOBTOLOGY_", env_file=".env")

    environment: Literal["development", "test", "production"] = "development"
    enable_fixtures: bool = False
    cors_origins: list[str] = ["http://localhost:5173"]

    @model_validator(mode="after")
    def reject_production_fixtures(self) -> "Settings":
        if self.environment == "production" and self.enable_fixtures:
            raise ValueError("Fixtures cannot be enabled in production")
        return self

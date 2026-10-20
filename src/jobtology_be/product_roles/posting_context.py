"""Curated search filters only; deliberately outside the approved competency policy."""

from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Occupation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title: str
    source: str


class PostingContextPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["posting-context-v1"]
    basis: Literal["APPLICATION_CONTEXT_SELECTION"]
    competency_evidence: Literal[False]
    roles: dict[str, list[Annotated[str, Field(pattern=r"^\d{2}-\d{4}\.\d{2}$")]]]
    occupations: dict[str, _Occupation]

    @model_validator(mode="after")
    def validate_codes(self) -> "PostingContextPolicy":
        for role_codes in self.roles.values():
            if not 1 <= len(role_codes) <= 5 or len(set(role_codes)) != len(role_codes):
                raise ValueError("Context codes must be bounded and unique")
            for value in role_codes:
                if value not in self.occupations:
                    raise ValueError("Unknown O*NET context occupation")
        return self


@lru_cache(maxsize=1)
def posting_context_policy() -> PostingContextPolicy:
    resource = files("jobtology_be.product_roles").joinpath("posting_context.v1.json")
    if not resource.is_file():
        resource = Path(__file__).resolve().parents[3] / "config/product_roles/posting_context.v1.json"
    return PostingContextPolicy.model_validate_json(resource.read_text())

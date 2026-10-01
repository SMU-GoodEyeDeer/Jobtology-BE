from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

type EntityKind = Literal[
    "occupation", "ncsCompetency", "organization", "jobPosting", "qualification",
    "examSession", "careerRank", "ncsUnitFamily", "ncsClass", "conceptScheme",
]

_SOURCE_FACT_KEYS = frozenset({
    "aliases", "source_status", "date_posted", "closing_date", "date_precision",
    "organization_id", "occupation_id", "qualification_id", "parent_id", "base_code",
    "version", "rank_level", "level", "depth", "taxonomy_version", "edition_policy",
    "label_status", "definition_status", "version_selection", "scheme_code", "year",
    "round", "category_code", "dates",
})


class CatalogRead(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore", strict=True)


class SourceProfile(CatalogRead):
    kind: Literal["SOURCE_ONLY"]
    analysis_available: Literal[False]
    capabilities: list[Literal["entities", "source_relations"]]


class CatalogContext(CatalogRead):
    contract_version: Literal["hop-catalog-source-v1"]
    release_id: str
    data_as_of: str | None
    manifest_hash: str
    source_profile: SourceProfile


class CatalogSummary(CatalogContext):
    entity_counts: dict[EntityKind, int]
    posting_selection_outcomes: dict[str, int]


class EntityListItem(CatalogRead):
    entity_id: str
    kind: EntityKind
    code: str
    scheme_id: str | None
    revision_id: str
    name: str | None
    payload_hash: str


class EntityDetail(EntityListItem):
    schema_version: str
    source_facts: dict[str, JsonValue]

    @field_validator("source_facts")
    @classmethod
    def allow_source_facts(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {key: fact for key, fact in value.items() if key in _SOURCE_FACT_KEYS}


class CatalogEntities(CatalogContext):
    entity_kind: EntityKind | None
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    items: list[EntityListItem]


class CatalogEntity(CatalogContext):
    entity: EntityDetail


class RelationItem(CatalogRead):
    relation_id: str
    subject_id: str
    predicate: str
    object_id: str
    assertion_kind: Literal["SOURCE_FACT"]
    acceptance_policy: str


class CatalogRelations(CatalogContext):
    entity_id: str
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    items: list[RelationItem]

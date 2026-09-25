from datetime import datetime
from typing import ClassVar, Literal, assert_never
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, StrictStr, model_validator


class RoadmapCreateRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "expected_profile_version": 3,
                    "goal_id": "00000000-0000-0000-0000-000000000001",
                    "proposal_id": "00000000-0000-0000-0000-000000000201",
                    "title": "Backend developer roadmap",
                }
            ]
        },
    )

    expected_profile_version: StrictInt = Field(ge=1)
    goal_id: UUID
    proposal_id: UUID
    title: StrictStr = Field(min_length=1, max_length=200)


class RoadmapMutationRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["ACTIVATE", "ARCHIVE", "RENAME"]
    expected_roadmap_version: StrictInt = Field(ge=1)
    expected_profile_version: StrictInt = Field(ge=1)
    title: StrictStr | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_rename_title(self) -> "RoadmapMutationRequest":
        match self.operation:
            case "RENAME":
                if self.title is None:
                    raise ValueError("rename requires a title")
            case "ACTIVATE" | "ARCHIVE":
                pass
            case unreachable:
                assert_never(unreachable)
        return self


class StepStateRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    state: Literal["TODO", "IN_PROGRESS", "COMPLETED"]
    expected_roadmap_version: StrictInt = Field(ge=1)
    expected_profile_version: StrictInt = Field(ge=1)


class RoadmapResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    roadmap_id: UUID
    roadmap_version: int = Field(ge=1)
    state: Literal["DRAFT", "ACTIVE", "ARCHIVED"]


class RoadmapStepResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    step_id: UUID
    step_key: str
    position: int
    action_id: str
    template_revision: int
    state: Literal["TODO", "IN_PROGRESS", "COMPLETED"]
    planned_start: datetime | None
    planned_end: datetime | None
    outcomes: tuple[JsonValue, ...]
    criteria: tuple[JsonValue, ...]
    prerequisite_step_ids: tuple[UUID, ...]


class RoadmapDetailResponse(RoadmapResponse):
    goal_id: UUID
    proposal_id: UUID
    title: str
    profile_version: int
    release_id: str | None
    validity: dict[str, JsonValue]
    steps: tuple[RoadmapStepResponse, ...]


class RoadmapListResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    items: tuple[RoadmapDetailResponse, ...]

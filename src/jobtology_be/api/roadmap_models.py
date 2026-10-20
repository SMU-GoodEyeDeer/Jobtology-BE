from datetime import datetime
from typing import ClassVar, Literal, assert_never
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    StrictStr,
    computed_field,
    model_validator,
)


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
    title: str | None
    description: str | None


class RoadmapDetailResponse(RoadmapResponse):
    analysis_id: UUID | None = None
    goal_id: UUID
    proposal_id: UUID
    title: str
    profile_version: int
    release_id: str | None
    validity: dict[str, JsonValue]
    steps: tuple[RoadmapStepResponse, ...]

    @computed_field
    @property
    def status(self) -> Literal["DRAFT", "ACTIVE", "ARCHIVED"]:
        return self.state

    @computed_field
    @property
    def version(self) -> int:
        return self.roadmap_version


class RoadmapListResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    items: tuple[RoadmapDetailResponse, ...]


class RoadmapRevisionRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True)
    proposal_id: UUID
    expected_proposal_hash: StrictStr = Field(min_length=1, max_length=128)
    expected_roadmap_version: StrictInt = Field(ge=1)
    expected_profile_version: StrictInt = Field(ge=1)
    expected_plan_version: StrictInt = Field(ge=0)
    title: StrictStr | None = Field(default=None, min_length=1, max_length=200)


class PreservedCompletedStepResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True, from_attributes=True)
    step_id: UUID
    step_key: str
    title: str


class RevisionScheduleChangeResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True, from_attributes=True)
    step_key: str
    old_planned_start: datetime | None
    old_planned_end: datetime | None
    new_planned_start: datetime | None
    new_planned_end: datetime | None


class RoadmapRevisionResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True, from_attributes=True)
    roadmap_id: UUID
    roadmap_version: int
    profile_version: int
    plan_version: int
    proposal_id: UUID
    proposal_hash: str
    analysis_id: UUID
    release_id: str | None
    feasibility: str
    can_apply: bool
    resets_future_customization: bool
    blocking_reasons: tuple[str, ...]
    preserved_completed_steps: tuple[PreservedCompletedStepResponse, ...]
    retained_in_progress_step_ids: tuple[UUID, ...]
    unretained_in_progress_step_ids: tuple[UUID, ...]
    added_step_keys: tuple[str, ...]
    removed_step_keys: tuple[str, ...]
    revised_step_count: int
    constraint_changes: dict[str, tuple[JsonValue, JsonValue]]
    schedule_changes: tuple[RevisionScheduleChangeResponse, ...]


class RoadmapRevisionAppliedResponse(RoadmapResponse):
    previous_roadmap_id: UUID
    preserved_completed_count: int

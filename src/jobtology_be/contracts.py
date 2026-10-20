import re
from datetime import date
from enum import StrEnum
from itertools import pairwise
from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    model_validator,
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WeeklyAvailabilityWindow(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)

    weekday: StrictInt = Field(ge=1, le=7)
    start_hour: StrictInt = Field(ge=0, le=23)
    end_hour: StrictInt = Field(ge=1, le=24)

    @model_validator(mode="after")
    def ordered_hours(self) -> Self:
        if self.end_hour <= self.start_hour:
            raise ValueError("availability end_hour must follow start_hour")
        return self


def _non_overlapping_windows(
    windows: tuple[WeeklyAvailabilityWindow, ...],
) -> tuple[WeeklyAvailabilityWindow, ...]:
    ordered = sorted(windows, key=lambda item: (item.weekday, item.start_hour))
    for previous, current in pairwise(ordered):
        if current.weekday == previous.weekday and current.start_hour < previous.end_hour:
            raise ValueError("weekly availability windows must not overlap")
    return tuple(ordered)


type WeeklyAvailability = Annotated[
    tuple[WeeklyAvailabilityWindow, ...], Field(max_length=168),
    AfterValidator(_non_overlapping_windows),
]


def _calendar_date(value: object) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("unavailable dates must use YYYY-MM-DD")
    return date.fromisoformat(value)


type UnavailableDate = Annotated[date, BeforeValidator(_calendar_date)]
type UnavailableDates = Annotated[tuple[UnavailableDate, ...], Field(max_length=366)]


class CapabilityInput(Contract):
    raw_text: str = Field(min_length=1, max_length=500)
    entity_id: str | None = None
    experience_codes: list[str] = Field(default_factory=list)
    # Internal domain/completion inputs default to full credit. Saved profile records
    # must derive this value from validated spec evidence, never copy a client score.
    credit: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False, strict=True)


class RoadmapOutcome(Contract):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    requirement_key: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    raw_text: str = Field(min_length=1, max_length=500)
    experience_codes: tuple[str, ...] = ()


class NormalizedCapability(Contract):
    raw_text: str
    entity_id: str | None
    experience_codes: list[str]
    resolution: Literal["RESOLVED", "UNRESOLVED", "AMBIGUOUS"]
    verification: Literal["SELF_REPORTED"] = "SELF_REPORTED"
    credit: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False, strict=True)


class NormalizedProfile(Contract):
    user_id: str
    profile_version: int = Field(ge=1)
    capabilities: list[NormalizedCapability]


class StepState(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class RequirementResult(Contract):
    requirement_key: str
    label: str
    currently_satisfied: bool
    support_refs: list[str]


class AnalysisPreview(Contract):
    analysis_id: str
    profile_version: int = Field(ge=1)
    basis_type: Literal["EDITORIAL", "MARKET"]
    basis_version: str
    corpus_release_id: str | None
    analysis_status: Literal["READY", "INSUFFICIENT_DATA"]
    requirements: list[RequirementResult]
    matched_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    is_fixture: bool = False


class ProposedStep(Contract):
    step_key: str
    action_id: str
    template_version: int
    title: str
    estimated_hours: int = Field(gt=0)
    prerequisite_step_keys: list[str]
    outcome_requirement_keys: list[str]
    completion_criteria: list[str]
    reason_codes: list[str]


class RoadmapPreview(Contract):
    route_proposal_id: str
    analysis_id: str
    profile_version: int
    basis_version: str
    feasibility: Literal["FEASIBLE", "RISKY", "PARTIAL", "INFEASIBLE"]
    proposed_steps: list[ProposedStep]
    is_fixture: bool = False

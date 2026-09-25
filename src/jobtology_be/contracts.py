from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityInput(Contract):
    raw_text: str = Field(min_length=1, max_length=500)
    entity_id: str | None = None
    experience_codes: list[str] = Field(default_factory=list)


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
    feasibility: Literal["FEASIBLE", "RISKY", "INFEASIBLE"]
    proposed_steps: list[ProposedStep]
    is_fixture: bool = False

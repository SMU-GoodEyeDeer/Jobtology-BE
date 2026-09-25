from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ProfileView:
    user_id: UUID
    profile_version: int
    major_raw: str
    major_concept_id: str | None
    year: int | None
    enrollment_status: str | None
    expected_graduation_on: date | None


@dataclass(frozen=True, slots=True)
class GoalView:
    goal_id: UUID
    goal_mode: str
    occupation_id: str | None
    target_by: datetime
    timezone: str
    original_time_phrase: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CapabilityView:
    capability_id: UUID
    category: str
    raw_text: str
    entity_id: str | None
    proficiency: str | None
    verification: str
    lifecycle: str
    details: Mapping[str, "ProductJsonValue"]
    source_completion_event_id: UUID | None
    created_at: datetime
    updated_at: datetime


type ProductJsonValue = (
    str
    | int
    | float
    | bool
    | None
    | tuple["ProductJsonValue", ...]
    | Mapping[str, "ProductJsonValue"]
)


@dataclass(frozen=True, slots=True)
class RoadmapScheduleChangeView:
    step_key: str
    old_planned_start: datetime | None
    old_planned_end: datetime | None
    new_planned_start: datetime | None
    new_planned_end: datetime | None


@dataclass(frozen=True, slots=True)
class RoadmapDiffView:
    roadmap_id: UUID
    roadmap_version: int
    proposal_id: UUID
    proposal_hash: str
    retained_step_keys: tuple[str, ...]
    added_step_keys: tuple[str, ...]
    removed_step_keys: tuple[str, ...]
    reordered_step_keys: tuple[str, ...]
    schedule_changes: tuple[RoadmapScheduleChangeView, ...]
    constraint_changes: Mapping[str, tuple[ProductJsonValue, ProductJsonValue]]


@dataclass(frozen=True, slots=True)
class RoadmapStepView:
    step_id: UUID
    step_key: str
    position: int
    action_id: str
    template_revision: int
    state: str
    planned_start: datetime | None
    planned_end: datetime | None
    outcomes: tuple[ProductJsonValue, ...]
    criteria: tuple[ProductJsonValue, ...]
    prerequisite_step_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class RoadmapView:
    roadmap_id: UUID
    roadmap_version: int
    state: str
    goal_id: UUID
    proposal_id: UUID
    title: str
    profile_version: int
    release_id: str | None
    validity: Mapping[str, ProductJsonValue]
    steps: tuple[RoadmapStepView, ...]


@dataclass(frozen=True, slots=True)
class RecomputeView:
    recompute_request_id: UUID
    profile_version: int
    state: str
    resulting_analysis_id: UUID | None
    proposal_id: UUID | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class AnalysisView:
    analysis_id: UUID
    goal_id: UUID
    profile_version: int
    basis_type: str
    basis_version: str
    release_id: str | None
    methodology_version: str
    status: str
    reference_at: datetime
    results: Mapping[str, ProductJsonValue] | None


class ProductQueries(Protocol):
    async def get_profile(self, user_id: UUID) -> ProfileView: ...

    async def list_goals(self, user_id: UUID) -> tuple[GoalView, ...]: ...

    async def get_goal(self, user_id: UUID, goal_id: UUID) -> GoalView: ...

    async def list_capabilities(self, user_id: UUID) -> tuple[CapabilityView, ...]: ...

    async def get_capability(self, user_id: UUID, capability_id: UUID) -> CapabilityView: ...

    async def list_roadmaps(self, user_id: UUID) -> tuple[RoadmapView, ...]: ...

    async def get_roadmap(self, user_id: UUID, roadmap_id: UUID) -> RoadmapView: ...

    async def get_roadmap_diff(
        self, user_id: UUID, roadmap_id: UUID, proposal_id: UUID
    ) -> RoadmapDiffView: ...

    async def request_analysis(
        self, user_id: UUID, goal_id: UUID, expected_profile_version: int
    ) -> RecomputeView: ...

    async def get_recompute(self, user_id: UUID, recompute_request_id: UUID) -> RecomputeView: ...

    async def get_analysis(self, user_id: UUID, analysis_id: UUID) -> AnalysisView: ...

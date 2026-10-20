"""Explicit comparison and atomic adoption of a revised proposal."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from jobtology_be.infrastructure.persistence.contracts import JsonValue


@dataclass(frozen=True, slots=True)
class PreservedCompletedStep:
    step_id: UUID
    step_key: str
    title: str


@dataclass(frozen=True, slots=True)
class RevisionScheduleChange:
    step_key: str
    old_planned_start: datetime | None
    old_planned_end: datetime | None
    new_planned_start: datetime | None
    new_planned_end: datetime | None


@dataclass(frozen=True, slots=True)
class RoadmapRevisionView:
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
    preserved_completed_steps: tuple[PreservedCompletedStep, ...]
    retained_in_progress_step_ids: tuple[UUID, ...]
    unretained_in_progress_step_ids: tuple[UUID, ...]
    added_step_keys: tuple[str, ...]
    removed_step_keys: tuple[str, ...]
    revised_step_count: int
    constraint_changes: Mapping[str, tuple[JsonValue, JsonValue]]
    schedule_changes: tuple[RevisionScheduleChange, ...]


@dataclass(frozen=True, slots=True)
class RoadmapRevisionApply:
    user_id: UUID
    roadmap_id: UUID
    proposal_id: UUID
    expected_proposal_hash: str
    expected_roadmap_version: int
    expected_profile_version: int
    expected_plan_version: int
    title: str | None = None


@dataclass(frozen=True, slots=True)
class RoadmapRevisionApplied:
    roadmap_id: UUID
    roadmap_version: int
    state: str
    previous_roadmap_id: UUID
    preserved_completed_count: int

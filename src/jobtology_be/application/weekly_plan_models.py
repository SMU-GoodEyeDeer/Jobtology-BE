"""Typed weekly plan boundaries; independent of persistence and HTTP."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from jobtology_be.application.queries import RoadmapStepView
from jobtology_be.application.requirement_metadata import RequirementMetadata


@dataclass(frozen=True, slots=True)
class PlanContext:
    roadmap_id: UUID
    created_at: datetime
    recommended_weekly_hours: int = 10
    target_date: date | None = None


@dataclass(frozen=True, slots=True)
class PlanStepInput:
    step: RoadmapStepView
    metadata: RequirementMetadata | None


class PlanModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True, strict=True)


class PlanOverrides(PlanModel):
    weekly_hours: int
    rest_weeks: tuple[int, ...]
    order: tuple[str, ...]
    hours: dict[str, int]
    saved_at: datetime


class WeeklyPlanStep(PlanModel):
    step_id: UUID
    number: int
    recommended_number: int
    title: str
    name: str
    requirement_key: str | None
    state: Literal['TODO', 'IN_PROGRESS', 'COMPLETED']
    phase: Literal['BASIC', 'CORE', 'ADVANCED']
    ncs_level: int | None
    field_code: str | None
    field_name: str | None
    hours: int
    recommended_hours: int
    hours_basis: Literal['OFFICIAL', 'ESTIMATED', 'CUSTOM']
    demand_pct: int | None
    start_week: int
    end_week: int
    start_date: date
    end_date: date
    reason: str
    moved: bool
    prerequisite_step_ids: tuple[UUID, ...]
    unmet_prerequisite_step_ids: tuple[UUID, ...]


class WeeklyPlanResponse(PlanModel):
    roadmap_id: UUID
    start_date: date
    target_date: date | None
    weekly_hours: int
    recommended_weekly_hours: int
    rest_weeks: tuple[int, ...]
    is_customized: bool
    saved_at: datetime | None
    total_hours: int
    total_weeks: int
    completion_date: date
    recommended_completion_date: date
    steps: tuple[WeeklyPlanStep, ...]

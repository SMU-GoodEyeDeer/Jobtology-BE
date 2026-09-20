from datetime import datetime
from typing import Literal, Protocol

from pydantic import AwareDatetime, Field

from jobtology_be.contracts import AnalysisPreview, Contract, ProposedStep, RoadmapPreview


class PlanningConstraints(Contract):
    target_by: AwareDatetime
    available_hours_per_week: int = Field(ge=1, le=60)
    budget_mode: Literal["REGULAR", "LOW_COST"] = "REGULAR"
    max_out_of_pocket_krw: int | None = Field(default=None, ge=0)
    fastest_path: bool = False
    needs_portfolio: bool = False
    career_switch: bool = False


class CandidateBuilder(Protocol):
    def build(self, analysis: AnalysisPreview) -> list[ProposedStep]: ...


class RoutePlanner(Protocol):
    def plan(
        self,
        analysis: AnalysisPreview,
        candidates: list[ProposedStep],
        constraints: PlanningConstraints,
        planning_started_at: datetime,
    ) -> RoadmapPreview: ...

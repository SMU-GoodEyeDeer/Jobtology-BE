from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from jobtology_be.infrastructure.persistence.contracts import JsonValue


class M5DataUnavailableError(Exception):
    resource: str

    def __init__(self, resource: str) -> None:
        super().__init__(resource)
        self.resource = resource


@dataclass(frozen=True, slots=True)
class OccupationView:
    occupation_id: str
    basis_version: str
    release_id: str
    reviewed_at: datetime | None


@dataclass(frozen=True, slots=True)
class RouteProposalView:
    proposal_id: UUID
    analysis_id: UUID
    decision_trace_id: UUID
    proposal_hash: str
    profile_version: int
    feasibility: str
    optimization_status: str
    constraints_snapshot: Mapping[str, JsonValue]
    steps: tuple[Mapping[str, JsonValue], ...]
    created_at: datetime
    basis_version: str
    release_id: str | None
    methodology_version: str


@dataclass(frozen=True, slots=True)
class TraceView:
    trace_id: UUID
    kind: str
    input_hash: str
    versions: Mapping[str, JsonValue]
    release_id: str | None
    outputs: Mapping[str, JsonValue]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DashboardAnalysisView:
    analysis_id: UUID
    profile_version: int
    basis_version: str
    release_id: str | None
    methodology_version: str
    status: str
    generated_at: datetime | None


@dataclass(frozen=True, slots=True)
class DashboardRoadmapView:
    roadmap_id: UUID
    roadmap_version: int
    proposal_id: UUID
    profile_version: int
    release_id: str | None
    title: str


@dataclass(frozen=True, slots=True)
class DashboardRecomputeView:
    recompute_request_id: UUID
    profile_version: int
    state: str
    error_code: str | None


@dataclass(frozen=True, slots=True)
class DashboardNextActionView:
    kind: str
    step_id: UUID
    title: str
    reason_code: str
    due_at: datetime | None


@dataclass(frozen=True, slots=True)
class DashboardEventView:
    kind: str
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DashboardView:
    state: Literal["EMPTY", "RECOMPUTING", "READY", "RECOMPUTE_FAILED"]
    goal_id: UUID | None
    analysis: DashboardAnalysisView | None
    active_roadmap: DashboardRoadmapView | None
    recompute: DashboardRecomputeView | None
    next_actions: tuple[DashboardNextActionView, ...]
    attention_items: tuple[str, ...]
    recent_events: tuple[DashboardEventView, ...]


class M5Queries(Protocol):
    async def get_occupations(self) -> tuple[OccupationView, ...]: ...

    async def get_route_proposal(self, user_id: UUID, proposal_id: UUID) -> RouteProposalView: ...

    async def get_trace(self, user_id: UUID, trace_id: UUID) -> TraceView: ...

    async def get_dashboard(self, user_id: UUID, goal_id: UUID | None) -> DashboardView: ...

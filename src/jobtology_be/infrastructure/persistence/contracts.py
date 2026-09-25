from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

type JsonValue = str | int | float | bool | None | Sequence[JsonValue] | Mapping[str, JsonValue]


@dataclass(slots=True)
class PersistenceConflictError(Exception):
    resource: str


@dataclass(slots=True)
class OwnershipError(Exception):
    resource: str


@dataclass(slots=True)
class MissingRecordError(Exception):
    resource: str


@dataclass(slots=True)
class IdempotencyConflictError(Exception):
    key: str


@dataclass(slots=True)
class IdempotencyInProgressError(Exception):
    key: str


@dataclass(frozen=True, slots=True)
class UserCreate:
    user_id: UUID


@dataclass(frozen=True, slots=True)
class ProfileMutation:
    user_id: UUID
    expected_version: int
    major_raw: str
    major_concept_id: str | None
    year: int | None
    enrollment_status: str | None
    expected_graduation_on: date | None


@dataclass(frozen=True, slots=True)
class CapabilityMutation:
    user_id: UUID
    expected_profile_version: int
    capability_id: UUID | None
    category: str
    raw_text: str
    entity_id: str | None
    proficiency: str | None
    details: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class CapabilityDelete:
    user_id: UUID
    capability_id: UUID
    expected_profile_version: int


@dataclass(frozen=True, slots=True)
class GoalMutation:
    user_id: UUID
    expected_profile_version: int
    goal_id: UUID | None
    goal_mode: str
    occupation_id: str | None
    target_by: datetime
    timezone: str
    original_time_phrase: str
    status: str


@dataclass(frozen=True, slots=True)
class RoutePreferenceMutation:
    user_id: UUID
    expected_profile_version: int
    available_hours_per_week: int
    availability_source: str
    budget_mode: str
    max_out_of_pocket_krw: int | None
    fastest_path: bool
    needs_portfolio: bool
    career_switch: bool


@dataclass(frozen=True, slots=True)
class RecomputeRequestCreate:
    user_id: UUID
    profile_version: int
    trigger_event_id: UUID
    dedupe_key: str
    payload: Mapping[str, JsonValue]
    context: Mapping[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class AnalysisRecomputeSubmission:
    user_id: UUID
    goal_id: UUID
    expected_profile_version: int
    dedupe_key: str
    payload: Mapping[str, JsonValue]
    context: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class AnalysisPersist:
    user_id: UUID
    analysis_id: UUID
    goal_id: UUID
    profile_version: int
    basis_type: str
    basis_version: str
    release_id: str | None
    methodology_version: str
    status: str
    reference_at: datetime
    input_snapshot: Mapping[str, JsonValue]
    input_hash: str
    results: Mapping[str, JsonValue] | None
    is_fixture: bool


@dataclass(frozen=True, slots=True)
class RouteProposalPersist:
    user_id: UUID
    proposal_id: UUID
    analysis_id: UUID
    profile_version: int
    proposal_hash: str
    constraints_snapshot: Mapping[str, JsonValue]
    feasibility: str
    optimization_status: str
    steps: Sequence[Mapping[str, JsonValue]]
    trace_id: UUID
    trace_versions: Mapping[str, JsonValue]
    trace_outputs: Mapping[str, JsonValue]
    release_id: str | None


@dataclass(frozen=True, slots=True)
class RoadmapCreate:
    user_id: UUID
    roadmap_id: UUID
    goal_id: UUID
    proposal_id: UUID
    title: str
    profile_version: int


@dataclass(frozen=True, slots=True)
class RoadmapMutation:
    user_id: UUID
    roadmap_id: UUID
    expected_roadmap_version: int
    expected_profile_version: int
    state: str
    title: str | None


@dataclass(frozen=True, slots=True)
class StepStateMutation:
    user_id: UUID
    roadmap_id: UUID
    step_id: UUID
    expected_roadmap_version: int
    expected_profile_version: int
    state: str


@dataclass(frozen=True, slots=True)
class IdempotencyAcquire:
    user_id: UUID
    method: str
    path: str
    key: str
    request_hash: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IdempotencyComplete:
    request: IdempotencyAcquire
    response_status: int
    response: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class OutboxLease:
    job_id: UUID
    lease_token: UUID
    kind: str
    payload: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class RecomputeWorkItem:
    request_id: UUID
    user_id: UUID
    profile_version: int
    lease: OutboxLease


@dataclass(frozen=True, slots=True)
class RecomputeArtifacts:
    analysis: AnalysisPersist
    proposal: RouteProposalPersist | None


@dataclass(frozen=True, slots=True)
class RecomputeFinalization:
    request_id: UUID
    analysis_id: UUID
    proposal_id: UUID | None
    updated_latest_analysis: bool


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    user_id: UUID
    version: int


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    capability_id: UUID
    profile_version: int


@dataclass(frozen=True, slots=True)
class GoalSnapshot:
    goal_id: UUID
    user_id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class RecomputeRequestSnapshot:
    request_id: UUID
    state: str


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    analysis_id: UUID
    profile_version: int
    status: str


@dataclass(frozen=True, slots=True)
class RouteProposalSnapshot:
    proposal_id: UUID
    feasibility: str


@dataclass(frozen=True, slots=True)
class RoadmapSnapshot:
    roadmap_id: UUID
    version: int
    state: str


@dataclass(frozen=True, slots=True)
class IdempotencySnapshot:
    response_status: int | None
    response: Mapping[str, JsonValue] | None

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Literal, assert_never, override

from jobtology_be.planning.candidate_models import (
    Candidate,
    CandidateBlocker,
    KnownKrwCost,
    UnknownKrwCost,
)
from jobtology_be.planning.contracts import PlanningConstraints

DEFAULT_SOLVER_TIME_LIMIT_SECONDS: Final = 20.0
OBJECTIVE_VERSION: Final = "CP_SAT_NORMALIZED_V1"
SLOT_DURATION: Final = timedelta(hours=1)
SOLVER_RANDOM_SEED: Final = 20_260_904
SOLVER_WORKER_COUNT: Final = 1


@dataclass(frozen=True, slots=True)
class InvalidPlanningProblemError(Exception):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


class RouteFeasibility(StrEnum):
    FEASIBLE = "FEASIBLE"
    RISKY = "RISKY"
    INFEASIBLE = "INFEASIBLE"


class OptimizationStatus(StrEnum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    TIMEOUT = "TIMEOUT"


class ObjectiveMethod(StrEnum):
    NORMALIZED_WEIGHTED = "NORMALIZED_WEIGHTED"


class UnknownCostTreatment(StrEnum):
    NO_LOW_COST_CREDIT = "NO_LOW_COST_CREDIT"


class RouteRejectionCode(StrEnum):
    INPUT_BLOCKER = "INPUT_BLOCKER"
    UNKNOWN_COST_WITH_HARD_CAP = "UNKNOWN_COST_WITH_HARD_CAP"
    NO_CANDIDATE_FOR_REQUIRED_REQUIREMENT = "NO_CANDIDATE_FOR_REQUIRED_REQUIREMENT"


class StepSelectionReason(StrEnum):
    SATISFIES_REQUIRED_REQUIREMENT = "SATISFIES_REQUIRED_REQUIREMENT"
    SATISFIES_PREFERRED_REQUIREMENT = "SATISFIES_PREFERRED_REQUIREMENT"
    PREREQUISITE = "PREREQUISITE"


@dataclass(frozen=True, slots=True)
class PlanningSlot:
    starts_at: datetime
    ends_at: datetime
    capacity_week_key: str

    def __post_init__(self) -> None:
        if not _is_aware(self.starts_at) or not _is_aware(self.ends_at):
            raise InvalidPlanningProblemError(reason="planning slots must use aware timestamps")
        if self.ends_at - self.starts_at != SLOT_DURATION:
            raise InvalidPlanningProblemError(reason="planning slots must be exactly one hour")
        if not self.capacity_week_key.strip():
            raise InvalidPlanningProblemError(reason="planning slot week key must be non-empty")


@dataclass(frozen=True, slots=True)
class CandidateAvailability:
    action_id: str
    available_from: datetime
    available_until: datetime

    def __post_init__(self) -> None:
        if not self.action_id.strip():
            raise InvalidPlanningProblemError(reason="candidate availability action ID must be non-empty")
        if not _is_aware(self.available_from) or not _is_aware(self.available_until):
            raise InvalidPlanningProblemError(reason="candidate availability must use aware timestamps")
        if self.available_until < self.available_from:
            raise InvalidPlanningProblemError(reason="candidate availability cannot end before it starts")


@dataclass(frozen=True, slots=True)
class SolverSettings:
    time_limit_seconds: float = DEFAULT_SOLVER_TIME_LIMIT_SECONDS

    def __post_init__(self) -> None:
        if self.time_limit_seconds < 0:
            raise InvalidPlanningProblemError(reason="solver time limit cannot be negative")


@dataclass(frozen=True, slots=True)
class PlanningConstraintSnapshot:
    target_by: datetime
    available_hours_per_week: int
    budget_mode: Literal["REGULAR", "LOW_COST"]
    max_out_of_pocket_krw: int | None
    fastest_path: bool
    needs_portfolio: bool
    career_switch: bool

    @classmethod
    def from_constraints(cls, constraints: PlanningConstraints) -> "PlanningConstraintSnapshot":
        return cls(
            target_by=constraints.target_by,
            available_hours_per_week=constraints.available_hours_per_week,
            budget_mode=constraints.budget_mode,
            max_out_of_pocket_krw=constraints.max_out_of_pocket_krw,
            fastest_path=constraints.fastest_path,
            needs_portfolio=constraints.needs_portfolio,
            career_switch=constraints.career_switch,
        )


@dataclass(frozen=True, slots=True)
class ObjectiveSnapshot:
    version: str
    method: ObjectiveMethod
    unknown_cost_treatment: UnknownCostTreatment
    coverage_weight: int
    time_weight: int
    cost_weight: int
    preferred_requirement_count: int
    horizon_microseconds: int
    known_cost_upper_bound_krw: int


@dataclass(frozen=True, slots=True)
class CandidateVersion:
    action_id: str
    template_revision: int


@dataclass(frozen=True, slots=True)
class SlotAllocation:
    slot_index: int
    capacity_week_key: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class ScheduledRouteStep:
    step_key: str
    action_id: str
    template_revision: int
    title: str
    estimated_hours: int
    prerequisite_step_keys: tuple[str, ...]
    outcome_requirement_keys: tuple[str, ...]
    completion_criteria: tuple[str, ...]
    support_refs: tuple[str, ...]
    reason_codes: tuple[StepSelectionReason, ...]
    planned_start_at: datetime
    planned_end_at: datetime
    slot_allocations: tuple[SlotAllocation, ...]


@dataclass(frozen=True, slots=True)
class RouteRejection:
    code: RouteRejectionCode
    action_id: str | None = None
    requirement_key: str | None = None


@dataclass(frozen=True, slots=True)
class PartialRouteDiagnostic:
    unmet_required_requirement_keys: tuple[str, ...]
    scheduled_steps: tuple[ScheduledRouteStep, ...]


@dataclass(frozen=True, slots=True)
class RoutePlanningTrace:
    analysis_id: str
    profile_version: int
    basis_version: str
    corpus_release_id: str | None
    is_fixture: bool
    reference_at: datetime
    planning_started_at: datetime
    constraints: PlanningConstraintSnapshot
    objective: ObjectiveSnapshot
    input_candidate_action_ids: tuple[str, ...]
    solver_candidate_action_ids: tuple[str, ...]
    candidate_versions: tuple[CandidateVersion, ...]
    solver_seed: int
    solver_worker_count: int
    time_limit_seconds: float
    optimization_status: OptimizationStatus
    rejections: tuple[RouteRejection, ...]
    partial_route_diagnostic: PartialRouteDiagnostic | None


@dataclass(frozen=True, slots=True)
class PlanningResult:
    feasibility: RouteFeasibility | None
    optimization_status: OptimizationStatus
    scheduled_steps: tuple[ScheduledRouteStep, ...]
    trace: RoutePlanningTrace


@dataclass(frozen=True, slots=True)
class PlanningProblem:
    analysis_id: str
    profile_version: int
    basis_version: str
    corpus_release_id: str | None
    is_fixture: bool
    reference_at: datetime
    candidates: tuple[Candidate, ...]
    candidate_blockers: tuple[CandidateBlocker, ...]
    required_requirement_keys: frozenset[str]
    preferred_requirement_keys: frozenset[str]
    constraints: PlanningConstraints
    planning_started_at: datetime
    calendar: tuple[PlanningSlot, ...]
    candidate_availability: tuple[CandidateAvailability, ...] = ()
    settings: SolverSettings = field(default_factory=SolverSettings)

    def __post_init__(self) -> None:
        if not self.analysis_id.strip() or not self.basis_version.strip():
            raise InvalidPlanningProblemError(reason="planning identity fields must be non-empty")
        if self.profile_version <= 0:
            raise InvalidPlanningProblemError(reason="profile version must be positive")
        if not _is_aware(self.reference_at) or not _is_aware(self.planning_started_at):
            raise InvalidPlanningProblemError(reason="planning timestamps must be aware")
        if self.constraints.target_by <= self.planning_started_at:
            raise InvalidPlanningProblemError(reason="planning target must be after planning start")
        if not self.calendar:
            raise InvalidPlanningProblemError(reason="planning calendar cannot be empty")
        if self.required_requirement_keys & self.preferred_requirement_keys:
            raise InvalidPlanningProblemError(reason="requirement necessity sets must not overlap")
        if tuple(sorted(self.calendar, key=lambda slot: slot.starts_at)) != self.calendar:
            raise InvalidPlanningProblemError(reason="planning calendar must be ordered")
        for prior, current in zip(self.calendar, self.calendar[1:]):
            if prior.ends_at > current.starts_at:
                raise InvalidPlanningProblemError(reason="planning calendar slots cannot overlap")
        if self.calendar[0].starts_at < self.planning_started_at:
            raise InvalidPlanningProblemError(reason="planning calendar cannot start before planning")
        if self.calendar[-1].ends_at > self.constraints.target_by:
            raise InvalidPlanningProblemError(reason="planning calendar cannot extend past target date")
        availability_action_ids = tuple(item.action_id for item in self.candidate_availability)
        if len(set(availability_action_ids)) != len(availability_action_ids):
            raise InvalidPlanningProblemError(reason="candidate availability must be unique per action")
        candidate_action_ids = frozenset(candidate.action_id for candidate in self.candidates)
        if any(action_id not in candidate_action_ids for action_id in availability_action_ids):
            raise InvalidPlanningProblemError(reason="candidate availability must reference a candidate")


def objective_snapshot(problem: PlanningProblem) -> ObjectiveSnapshot:
    match problem.constraints.budget_mode:
        case "REGULAR":
            coverage_weight, time_weight, cost_weight = 55, 30, 15
        case "LOW_COST":
            coverage_weight, time_weight, cost_weight = 35, 20, 45
        case unreachable:
            assert_never(unreachable)
    if problem.constraints.fastest_path:
        coverage_weight -= 10
        time_weight += 15
        cost_weight -= 5
    return ObjectiveSnapshot(
        version=OBJECTIVE_VERSION,
        method=ObjectiveMethod.NORMALIZED_WEIGHTED,
        unknown_cost_treatment=UnknownCostTreatment.NO_LOW_COST_CREDIT,
        coverage_weight=coverage_weight,
        time_weight=time_weight,
        cost_weight=cost_weight,
        preferred_requirement_count=len(problem.preferred_requirement_keys),
        horizon_microseconds=_microseconds_between(
            problem.planning_started_at, problem.constraints.target_by
        ),
        known_cost_upper_bound_krw=sum(_known_cost(candidate) for candidate in problem.candidates),
    )


def _known_cost(candidate: Candidate) -> int:
    match candidate.cost:
        case KnownKrwCost(krw=krw):
            return krw
        case UnknownKrwCost():
            return 0
        case unreachable:
            assert_never(unreachable)


def microseconds_from_planning_start(problem: PlanningProblem, value: datetime) -> int:
    return _microseconds_between(problem.planning_started_at, value)


def _microseconds_between(start: datetime, end: datetime) -> int:
    delta = end - start
    return ((delta.days * 86_400) + delta.seconds) * 1_000_000 + delta.microseconds


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

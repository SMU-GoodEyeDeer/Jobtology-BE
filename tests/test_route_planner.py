from datetime import UTC, datetime, timedelta

from jobtology_be.planning.candidate_models import Candidate, KnownKrwCost, UnknownKrwCost
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.planning.cp_sat_planner import CpSatRoutePlanner
from jobtology_be.planning.solver_models import (
    OBJECTIVE_VERSION,
    SOLVER_RANDOM_SEED,
    CandidateAvailability,
    CandidateVersion,
    OptimizationStatus,
    PlanningProblem,
    PlanningSlot,
    RouteFeasibility,
    SolverSettings,
    UnknownCostTreatment,
)

REFERENCE_TIME = datetime(2026, 9, 22, 9, tzinfo=UTC)


def candidate(
    action_id: str,
    outcomes: frozenset[str],
    *,
    estimated_hours: int = 1,
    prerequisites: tuple[str, ...] = (),
    cost: KnownKrwCost | UnknownKrwCost | None = None,
) -> Candidate:
    return Candidate(
        action_id=action_id,
        template_revision=1,
        title=f"Synthetic {action_id}",
        estimated_hours=estimated_hours,
        outcome_requirement_keys=outcomes,
        prerequisite_action_ids=prerequisites,
        fulfilled_prerequisite_action_ids=(),
        completion_criteria=("synthetic completion",),
        support_refs=frozenset({"synthetic:template"}),
        cost=KnownKrwCost(krw=0) if cost is None else cost,
    )


def calendar(*weekly_hours: int) -> tuple[PlanningSlot, ...]:
    slots: list[PlanningSlot] = []
    for week_index, available_hours in enumerate(weekly_hours):
        week_start = REFERENCE_TIME + timedelta(days=7 * week_index)
        slots.extend(
            PlanningSlot(
                starts_at=week_start + timedelta(hours=hour_index),
                ends_at=week_start + timedelta(hours=hour_index + 1),
                capacity_week_key=f"week-{week_index}",
            )
            for hour_index in range(available_hours)
        )
    return tuple(slots)


def problem(
    candidates: tuple[Candidate, ...],
    *,
    required: frozenset[str] | None = None,
    preferred: frozenset[str] | None = None,
    slots: tuple[PlanningSlot, ...] | None = None,
    max_out_of_pocket_krw: int | None = None,
    availability: tuple[CandidateAvailability, ...] = (),
    settings: SolverSettings | None = None,
) -> PlanningProblem:
    resolved_slots = calendar(3, 3, 3) if slots is None else slots
    return PlanningProblem(
        analysis_id="analysis-1",
        profile_version=4,
        basis_version="reviewed-v1",
        corpus_release_id=None,
        is_fixture=True,
        reference_at=REFERENCE_TIME,
        candidates=candidates,
        candidate_blockers=(),
        required_requirement_keys=frozenset({"api"}) if required is None else required,
        preferred_requirement_keys=frozenset() if preferred is None else preferred,
        constraints=PlanningConstraints(
            target_by=resolved_slots[-1].ends_at + timedelta(days=7),
            available_hours_per_week=3,
            max_out_of_pocket_krw=max_out_of_pocket_krw,
        ),
        planning_started_at=REFERENCE_TIME,
        calendar=resolved_slots,
        candidate_availability=availability,
        settings=SolverSettings() if settings is None else settings,
    )


def test_plan_orders_prerequisites_within_same_capacity_week() -> None:
    # Given
    route = candidate("z-api-route", frozenset({"api"}), prerequisites=("foundation",))
    foundation = candidate("foundation", frozenset())
    request = problem((route, foundation), slots=calendar(3))

    # When
    result = CpSatRoutePlanner().plan(request)

    # Then
    assert result.feasibility is RouteFeasibility.FEASIBLE
    assert result.optimization_status is OptimizationStatus.OPTIMAL
    assert tuple(step.action_id for step in result.scheduled_steps) == ("foundation", "z-api-route")
    assert result.scheduled_steps[0].planned_end_at <= result.scheduled_steps[1].planned_start_at
    assert result.scheduled_steps[0].slot_allocations[0].capacity_week_key == "week-0"
    assert result.scheduled_steps[1].slot_allocations[0].capacity_week_key == "week-0"
    assert result.trace.input_candidate_action_ids == ("z-api-route", "foundation")
    assert result.trace.solver_candidate_action_ids == ("foundation", "z-api-route")


def test_plan_excludes_unknown_cost_candidates_under_hard_cap() -> None:
    # Given
    cheap = candidate("cheap", frozenset({"api"}), cost=KnownKrwCost(krw=40))
    expensive = candidate("expensive", frozenset({"api"}), cost=KnownKrwCost(krw=90))
    unknown = candidate("unknown", frozenset({"api"}), cost=UnknownKrwCost())
    request = problem((unknown, expensive, cheap), max_out_of_pocket_krw=50)

    # When
    result = CpSatRoutePlanner().plan(request)

    # Then
    assert result.feasibility is RouteFeasibility.FEASIBLE
    assert tuple(step.action_id for step in result.scheduled_steps) == ("cheap",)
    assert {rejection.action_id for rejection in result.trace.rejections} == {"unknown"}


def test_plan_uses_only_full_slots_inside_partial_calendar_window() -> None:
    # Given
    slots = calendar(3, 3)
    api = candidate("api", frozenset({"api"}))
    request = problem(
        (api,),
        slots=slots,
        availability=(
            CandidateAvailability(
                action_id="api",
                available_from=slots[3].starts_at + timedelta(minutes=30),
                available_until=slots[5].ends_at,
            ),
        ),
    )

    # When
    result = CpSatRoutePlanner().plan(request)

    # Then
    assert result.feasibility is RouteFeasibility.FEASIBLE
    assert result.scheduled_steps[0].planned_start_at == slots[4].starts_at


def test_plan_marks_unknown_cost_route_as_risky_without_hard_cap() -> None:
    # Given
    request = problem((candidate("api", frozenset({"api"}), cost=UnknownKrwCost()),))

    # When
    result = CpSatRoutePlanner().plan(request)

    # Then
    assert result.feasibility is RouteFeasibility.RISKY
    assert result.optimization_status is OptimizationStatus.OPTIMAL
    assert result.trace.objective.unknown_cost_treatment is UnknownCostTreatment.NO_LOW_COST_CREDIT


def test_plan_returns_infeasible_with_partial_diagnostic_after_proof() -> None:
    # Given
    request = problem((candidate("api", frozenset({"api"}), estimated_hours=4),), slots=calendar(3))

    # When
    result = CpSatRoutePlanner().plan(request)

    # Then
    assert result.feasibility is RouteFeasibility.INFEASIBLE
    assert result.optimization_status is OptimizationStatus.INFEASIBLE
    assert result.scheduled_steps == ()
    assert result.trace.partial_route_diagnostic is not None
    assert result.trace.partial_route_diagnostic.unmet_required_requirement_keys == ("api",)


def test_plan_distinguishes_solver_timeout_from_infeasibility() -> None:
    # Given
    request = problem((candidate("api", frozenset({"api"}),),), settings=SolverSettings(0))

    # When
    result = CpSatRoutePlanner().plan(request)

    # Then
    assert result.feasibility is None
    assert result.optimization_status is OptimizationStatus.TIMEOUT
    assert result.trace.partial_route_diagnostic is None


def test_plan_replays_with_pinned_objective_and_solver_inputs() -> None:
    # Given
    request = problem((candidate("api", frozenset({"api"}),),))
    planner = CpSatRoutePlanner()

    # When
    first = planner.plan(request)
    replay = planner.plan(request)

    # Then
    assert replay == first
    assert first.trace.profile_version == 4
    assert first.trace.basis_version == "reviewed-v1"
    assert first.trace.solver_seed == SOLVER_RANDOM_SEED
    assert first.trace.candidate_versions == (CandidateVersion(action_id="api", template_revision=1),)
    assert first.trace.objective.version == OBJECTIVE_VERSION

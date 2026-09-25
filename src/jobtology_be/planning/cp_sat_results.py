from typing import assert_never

from ortools.sat.python import cp_model

from jobtology_be.planning.candidate_models import Candidate, KnownKrwCost, UnknownKrwCost
from jobtology_be.planning.cp_sat_model import RouteModelArtifacts
from jobtology_be.planning.solver_models import (
    SOLVER_RANDOM_SEED,
    SOLVER_WORKER_COUNT,
    CandidateVersion,
    OptimizationStatus,
    PartialRouteDiagnostic,
    PlanningConstraintSnapshot,
    PlanningProblem,
    PlanningResult,
    RouteFeasibility,
    RoutePlanningTrace,
    RouteRejection,
    ScheduledRouteStep,
    SlotAllocation,
    StepSelectionReason,
    objective_snapshot,
)


def scheduled_steps(
    problem: PlanningProblem, artifacts: RouteModelArtifacts, solver: cp_model.CpSolver
) -> tuple[ScheduledRouteStep, ...]:
    candidates_by_action = {candidate.action_id: candidate for candidate in artifacts.candidates}
    selected_candidates = tuple(
        candidate
        for candidate in artifacts.candidates
        if solver.value(artifacts.selected_by_action[candidate.action_id]) == 1
    )
    steps: list[ScheduledRouteStep] = []
    for candidate in selected_candidates:
        allocations = tuple(
            SlotAllocation(
                slot_index=slot_index,
                capacity_week_key=problem.calendar[slot_index].capacity_week_key,
                starts_at=problem.calendar[slot_index].starts_at,
                ends_at=problem.calendar[slot_index].ends_at,
            )
            for slot_index, assignment in enumerate(artifacts.assignments_by_action[candidate.action_id])
            if solver.value(assignment) == 1
        )
        steps.append(
            ScheduledRouteStep(
                step_key=_step_key(candidate),
                action_id=candidate.action_id,
                template_revision=candidate.template_revision,
                title=candidate.title,
                estimated_hours=candidate.estimated_hours,
                prerequisite_step_keys=tuple(
                    _step_key(candidates_by_action[action_id])
                    for action_id in candidate.prerequisite_action_ids
                ),
                outcome_requirement_keys=tuple(sorted(candidate.outcome_requirement_keys)),
                completion_criteria=candidate.completion_criteria,
                support_refs=tuple(sorted(candidate.support_refs)),
                reason_codes=_selection_reasons(problem, candidate, selected_candidates),
                planned_start_at=allocations[0].starts_at,
                planned_end_at=allocations[-1].ends_at,
                slot_allocations=allocations,
            )
        )
    return tuple(sorted(steps, key=lambda step: (step.planned_start_at, step.action_id)))


def route_feasibility(
    problem: PlanningProblem, steps: tuple[ScheduledRouteStep, ...]
) -> RouteFeasibility:
    selected_actions = frozenset(step.action_id for step in steps)
    has_unknown_cost = any(
        _has_unknown_cost(candidate)
        for candidate in problem.candidates
        if candidate.action_id in selected_actions
    )
    completion_at = max((step.planned_end_at for step in steps), default=problem.planning_started_at)
    horizon = problem.constraints.target_by - problem.planning_started_at
    elapsed = completion_at - problem.planning_started_at
    if not has_unknown_cost and elapsed * 5 <= horizon * 4:
        return RouteFeasibility.FEASIBLE
    return RouteFeasibility.RISKY


def planning_result(
    *,
    problem: PlanningProblem,
    candidates: tuple[Candidate, ...],
    feasibility: RouteFeasibility | None,
    status: OptimizationStatus,
    scheduled_steps: tuple[ScheduledRouteStep, ...],
    rejections: tuple[RouteRejection, ...],
    diagnostic: PartialRouteDiagnostic | None,
) -> PlanningResult:
    trace = RoutePlanningTrace(
        analysis_id=problem.analysis_id,
        profile_version=problem.profile_version,
        basis_version=problem.basis_version,
        corpus_release_id=problem.corpus_release_id,
        is_fixture=problem.is_fixture,
        reference_at=problem.reference_at,
        planning_started_at=problem.planning_started_at,
        constraints=PlanningConstraintSnapshot.from_constraints(problem.constraints),
        objective=objective_snapshot(problem),
        input_candidate_action_ids=tuple(candidate.action_id for candidate in problem.candidates),
        solver_candidate_action_ids=tuple(candidate.action_id for candidate in candidates),
        candidate_versions=tuple(
            CandidateVersion(candidate.action_id, candidate.template_revision) for candidate in candidates
        ),
        solver_seed=SOLVER_RANDOM_SEED,
        solver_worker_count=SOLVER_WORKER_COUNT,
        time_limit_seconds=problem.settings.time_limit_seconds,
        optimization_status=status,
        rejections=rejections,
        partial_route_diagnostic=diagnostic,
    )
    return PlanningResult(
        feasibility=feasibility,
        optimization_status=status,
        scheduled_steps=scheduled_steps,
        trace=trace,
    )


def _selection_reasons(
    problem: PlanningProblem, candidate: Candidate, selected_candidates: tuple[Candidate, ...]
) -> tuple[StepSelectionReason, ...]:
    reasons: set[StepSelectionReason] = set()
    if candidate.outcome_requirement_keys & problem.required_requirement_keys:
        reasons.add(StepSelectionReason.SATISFIES_REQUIRED_REQUIREMENT)
    if candidate.outcome_requirement_keys & problem.preferred_requirement_keys:
        reasons.add(StepSelectionReason.SATISFIES_PREFERRED_REQUIREMENT)
    if any(candidate.action_id in selected.prerequisite_action_ids for selected in selected_candidates):
        reasons.add(StepSelectionReason.PREREQUISITE)
    return tuple(sorted(reasons))


def _step_key(candidate: Candidate) -> str:
    return f"{candidate.action_id}@{candidate.template_revision}"


def _has_unknown_cost(candidate: Candidate) -> bool:
    match candidate.cost:
        case KnownKrwCost():
            return False
        case UnknownKrwCost():
            return True
        case unreachable:
            assert_never(unreachable)

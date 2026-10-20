import time
from collections import Counter
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Literal, assert_never, override

from ortools.sat.python import cp_model

from jobtology_be.planning.candidate_models import (
    Candidate,
    InsufficientAnalysisBlocker,
    KnownKrwCost,
    NeedsInputBlocker,
    NoActivitiesBlocker,
    UnknownKrwCost,
)
from jobtology_be.planning.cp_sat_model import build_route_model
from jobtology_be.planning.cp_sat_results import planning_result, route_feasibility, scheduled_steps
from jobtology_be.planning.solver_models import (
    SOLVER_RANDOM_SEED,
    SOLVER_WORKER_COUNT,
    InvalidPlanningProblemError,
    OptimizationStatus,
    PartialRouteDiagnostic,
    PlanningProblem,
    PlanningResult,
    RouteFeasibility,
    RouteRejection,
    RouteRejectionCode,
    SolverRunTelemetry,
    SolverSettings,
)


@dataclass(frozen=True, slots=True)
class CpSatRoutePlanner:
    worker_count: int = SOLVER_WORKER_COUNT

    def __post_init__(self) -> None:
        if not isinstance(self.worker_count, int) or isinstance(self.worker_count, bool) or self.worker_count < 1:
            raise InvalidPlanningProblemError(reason='solver worker count must be positive')

    def plan(self, problem: PlanningProblem) -> PlanningResult:
        candidates = tuple(sorted(problem.candidates, key=lambda candidate: candidate.action_id))
        rejections, is_preflight_infeasible = _preflight(problem, candidates)
        if is_preflight_infeasible:
            return planning_result(
                worker_count=self.worker_count,
                problem=problem,
                candidates=candidates,
                feasibility=RouteFeasibility.INFEASIBLE,
                status=OptimizationStatus.INFEASIBLE,
                scheduled_steps=(),
                rejections=rejections,
                diagnostic=None,
                solver_runs=(SolverRunTelemetry(
                    phase="FULL", status="INFEASIBLE", time_limit_seconds=0, wall_time_seconds=0,
                    skipped_reason="INPUT_REJECTION",
                ),),
            )
        # Full and partial searches share one budget. Previously an unknown/full
        # infeasible result could trigger a second complete time allowance.
        deadline = time.monotonic() + problem.settings.time_limit_seconds
        full_problem = replace(problem, settings=SolverSettings(
            time_limit_seconds=problem.settings.time_limit_seconds * .75,
        ))
        if _full_capacity_possible(problem, candidates):
            artifacts = build_route_model(problem, candidates, require_required_coverage=True)
            full_problem = replace(full_problem, settings=SolverSettings(time_limit_seconds=min(
                full_problem.settings.time_limit_seconds, max(0.0, deadline - time.monotonic()),
            )))
            solver, status = _solve(artifacts.model, full_problem, self.worker_count)
            full_run = _telemetry(solver, status, full_problem, "FULL")
        else:
            status = cp_model.INFEASIBLE
            full_run = SolverRunTelemetry(
                phase="FULL", status="INFEASIBLE", time_limit_seconds=0,
                wall_time_seconds=0, skipped_reason="REQUIRED_EFFORT_EXCEEDS_CALENDAR_CAPACITY",
            )
        match status:
            case cp_model.OPTIMAL:
                solved_steps = scheduled_steps(problem, artifacts, solver)
                return planning_result(
                    worker_count=self.worker_count,
                    problem=problem,
                    candidates=candidates,
                    feasibility=route_feasibility(problem, solved_steps),
                    status=OptimizationStatus.OPTIMAL,
                    scheduled_steps=solved_steps,
                    rejections=rejections,
                    diagnostic=None,
                    solver_runs=(full_run,),
                )
            case cp_model.FEASIBLE:
                solved_steps = scheduled_steps(problem, artifacts, solver)
                return planning_result(
                    worker_count=self.worker_count,
                    problem=problem,
                    candidates=candidates,
                    feasibility=route_feasibility(problem, solved_steps),
                    status=OptimizationStatus.FEASIBLE,
                    scheduled_steps=solved_steps,
                    rejections=rejections,
                    diagnostic=None,
                    solver_runs=(full_run,),
                )
            case cp_model.INFEASIBLE:
                diagnostic = _partial_diagnostic(problem, candidates, self.worker_count, deadline=deadline)
                if diagnostic.scheduled_steps:
                    return planning_result(
                        worker_count=self.worker_count,
                        problem=problem,
                        candidates=candidates,
                        feasibility=RouteFeasibility.PARTIAL,
                        status=OptimizationStatus.INFEASIBLE,
                        scheduled_steps=diagnostic.scheduled_steps,
                        rejections=rejections,
                        diagnostic=diagnostic,
                        solver_runs=_runs(full_run, diagnostic),
                    )
                return planning_result(
                    worker_count=self.worker_count,
                    problem=problem,
                    candidates=candidates,
                    feasibility=RouteFeasibility.INFEASIBLE,
                    status=OptimizationStatus.INFEASIBLE,
                    scheduled_steps=(),
                    rejections=rejections,
                    diagnostic=diagnostic,
                    solver_runs=_runs(full_run, diagnostic),
                )
            case cp_model.UNKNOWN:
                # No full-coverage route was found in time. Fall back to the best route
                # without the coverage constraint; it is a regular route only when it
                # still covers every required requirement.
                diagnostic = _partial_diagnostic(problem, candidates, self.worker_count, deadline=deadline)
                if not diagnostic.scheduled_steps:
                    return planning_result(
                        worker_count=self.worker_count,
                        problem=problem,
                        candidates=candidates,
                        feasibility=None,
                        status=OptimizationStatus.TIMEOUT,
                        scheduled_steps=(),
                        rejections=rejections,
                        diagnostic=None,
                        solver_runs=_runs(full_run, diagnostic),
                    )
                if diagnostic.unmet_required_requirement_keys:
                    return planning_result(
                        worker_count=self.worker_count,
                        problem=problem,
                        candidates=candidates,
                        feasibility=RouteFeasibility.PARTIAL,
                        status=OptimizationStatus.TIMEOUT,
                        scheduled_steps=diagnostic.scheduled_steps,
                        rejections=rejections,
                        diagnostic=diagnostic,
                        solver_runs=_runs(full_run, diagnostic),
                    )
                return planning_result(
                    worker_count=self.worker_count,
                    problem=problem,
                    candidates=candidates,
                    feasibility=route_feasibility(problem, diagnostic.scheduled_steps),
                    status=OptimizationStatus.FEASIBLE,
                    scheduled_steps=diagnostic.scheduled_steps,
                    rejections=rejections,
                    diagnostic=None,
                    solver_runs=_runs(full_run, diagnostic),
                )
            case _:
                raise UnexpectedCpSatStatusError(status=status)


@dataclass(frozen=True, slots=True)
class UnexpectedCpSatStatusError(Exception):
    status: int

    @override
    def __str__(self) -> str:
        return f"unexpected CP-SAT status: {self.status}"


def _remaining_problem(problem: PlanningProblem, deadline: float) -> PlanningProblem:
    return replace(problem, settings=SolverSettings(
        time_limit_seconds=max(0.0, deadline - time.monotonic()),
    ))


def _solve(
    model: cp_model.CpModel, problem: PlanningProblem, worker_count: int
) -> tuple[cp_model.CpSolver, int]:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = problem.settings.time_limit_seconds
    solver.parameters.num_search_workers = worker_count
    solver.parameters.random_seed = SOLVER_RANDOM_SEED
    return solver, solver.solve(model)


def _preflight(
    problem: PlanningProblem, candidates: tuple[Candidate, ...]
) -> tuple[tuple[RouteRejection, ...], bool]:
    rejections: list[RouteRejection] = []
    is_infeasible = False
    for blocker in problem.candidate_blockers:
        match blocker:
            case InsufficientAnalysisBlocker():
                rejections.append(RouteRejection(code=RouteRejectionCode.INPUT_BLOCKER))
                is_infeasible = True
            case NeedsInputBlocker(requirement_key=requirement_key):
                rejections.append(
                    RouteRejection(
                        code=RouteRejectionCode.INPUT_BLOCKER, requirement_key=requirement_key
                    )
                )
                is_infeasible = is_infeasible or requirement_key in problem.required_requirement_keys
            case NoActivitiesBlocker(requirement_key=requirement_key):
                rejections.append(
                    RouteRejection(
                        code=RouteRejectionCode.INPUT_BLOCKER, requirement_key=requirement_key
                    )
                )
                is_infeasible = is_infeasible or requirement_key in problem.required_requirement_keys
            case unreachable:
                assert_never(unreachable)
    if problem.constraints.max_out_of_pocket_krw is not None:
        rejections.extend(
            RouteRejection(
                code=RouteRejectionCode.UNKNOWN_COST_WITH_HARD_CAP, action_id=candidate.action_id
            )
            for candidate in candidates
            if _has_unknown_cost(candidate)
        )
    candidate_outcomes = frozenset().union(
        *(candidate.outcome_requirement_keys for candidate in candidates)
    )
    for requirement_key in sorted(problem.required_requirement_keys - candidate_outcomes):
        rejections.append(
            RouteRejection(
                code=RouteRejectionCode.NO_CANDIDATE_FOR_REQUIRED_REQUIREMENT,
                requirement_key=requirement_key,
            )
        )
        is_infeasible = True
    return tuple(rejections), is_infeasible


def _has_unknown_cost(candidate: Candidate) -> bool:
    match candidate.cost:
        case KnownKrwCost():
            return False
        case UnknownKrwCost():
            return True
        case unreachable:
            assert_never(unreachable)


def _partial_diagnostic(
    problem: PlanningProblem, candidates: tuple[Candidate, ...], worker_count: int, *, deadline: float,
) -> PartialRouteDiagnostic:
    artifacts = build_route_model(problem, candidates, require_required_coverage=False)
    remaining = _remaining_problem(problem, deadline)
    solver, status = _solve(artifacts.model, remaining, worker_count)
    run = _telemetry(solver, status, remaining, "PARTIAL")
    match status:
        case cp_model.OPTIMAL | cp_model.FEASIBLE:
            unmet = tuple(
                key
                for key in sorted(problem.required_requirement_keys)
                if solver.value(artifacts.covered_by_requirement[key]) == 0
            )
            return PartialRouteDiagnostic(
                unmet_required_requirement_keys=unmet,
                scheduled_steps=scheduled_steps(problem, artifacts, solver),
                solver_run=run,
            )
        case cp_model.INFEASIBLE | cp_model.UNKNOWN:
            return PartialRouteDiagnostic(
                unmet_required_requirement_keys=tuple(sorted(problem.required_requirement_keys)),
                scheduled_steps=(),
                solver_run=run,
            )
        case _:
            raise UnexpectedCpSatStatusError(status=status)


def _telemetry(solver: cp_model.CpSolver, status: int, problem: PlanningProblem,
               phase: Literal["FULL", "PARTIAL"]) -> SolverRunTelemetry:
    solved = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    try:
        wall_time = solver.wall_time
        bound = solver.best_objective_bound
    except RuntimeError:
        # Test/adaptor implementations may report UNKNOWN without a solve response.
        wall_time, bound = 0.0, None
    return SolverRunTelemetry(
        phase=phase, status=solver.status_name(status),
        time_limit_seconds=problem.settings.time_limit_seconds,
        wall_time_seconds=wall_time,
        objective_value=solver.objective_value if solved else None,
        best_objective_bound=bound,
    )


def _runs(full: SolverRunTelemetry, partial: PartialRouteDiagnostic) -> tuple[SolverRunTelemetry, ...]:
    return (full, partial.solver_run) if partial.solver_run is not None else (full,)


def _full_capacity_possible(problem: PlanningProblem, candidates: tuple[Candidate, ...]) -> bool:
    """A conservative effort lower bound, never a replacement for CP-SAT."""
    capacity = sum(min(count, problem.constraints.available_hours_per_week)
                   for count in Counter(slot.capacity_week_key for slot in problem.calendar).values())
    # Divide each activity's effort among all its required outcomes. Summing the
    # cheapest share for each requirement cannot exceed a covering route's effort,
    # including when one activity covers multiple requirements.
    lower_bound = sum((min((Fraction(candidate.estimated_hours, len(
        candidate.outcome_requirement_keys & problem.required_requirement_keys))
        for candidate in candidates if key in candidate.outcome_requirement_keys),
        default=Fraction(capacity + 1)) for key in problem.required_requirement_keys), Fraction())
    return lower_bound <= capacity

from dataclasses import dataclass
from typing import assert_never, override

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
    OptimizationStatus,
    PartialRouteDiagnostic,
    PlanningProblem,
    PlanningResult,
    RouteFeasibility,
    RouteRejection,
    RouteRejectionCode,
)


@dataclass(frozen=True, slots=True)
class CpSatRoutePlanner:
    def plan(self, problem: PlanningProblem) -> PlanningResult:
        candidates = tuple(sorted(problem.candidates, key=lambda candidate: candidate.action_id))
        rejections, is_preflight_infeasible = _preflight(problem, candidates)
        if is_preflight_infeasible:
            return planning_result(
                problem=problem,
                candidates=candidates,
                feasibility=RouteFeasibility.INFEASIBLE,
                status=OptimizationStatus.INFEASIBLE,
                scheduled_steps=(),
                rejections=rejections,
                diagnostic=None,
            )
        artifacts = build_route_model(
            problem, candidates, require_required_coverage=True
        )
        solver, status = _solve(artifacts.model, problem)
        match status:
            case cp_model.OPTIMAL:
                solved_steps = scheduled_steps(problem, artifacts, solver)
                return planning_result(
                    problem=problem,
                    candidates=candidates,
                    feasibility=route_feasibility(problem, solved_steps),
                    status=OptimizationStatus.OPTIMAL,
                    scheduled_steps=solved_steps,
                    rejections=rejections,
                    diagnostic=None,
                )
            case cp_model.FEASIBLE:
                solved_steps = scheduled_steps(problem, artifacts, solver)
                return planning_result(
                    problem=problem,
                    candidates=candidates,
                    feasibility=route_feasibility(problem, solved_steps),
                    status=OptimizationStatus.FEASIBLE,
                    scheduled_steps=solved_steps,
                    rejections=rejections,
                    diagnostic=None,
                )
            case cp_model.INFEASIBLE:
                diagnostic = _partial_diagnostic(problem, candidates)
                return planning_result(
                    problem=problem,
                    candidates=candidates,
                    feasibility=RouteFeasibility.INFEASIBLE,
                    status=OptimizationStatus.INFEASIBLE,
                    scheduled_steps=(),
                    rejections=rejections,
                    diagnostic=diagnostic,
                )
            case cp_model.UNKNOWN:
                return planning_result(
                    problem=problem,
                    candidates=candidates,
                    feasibility=None,
                    status=OptimizationStatus.TIMEOUT,
                    scheduled_steps=(),
                    rejections=rejections,
                    diagnostic=None,
                )
            case _:
                raise UnexpectedCpSatStatusError(status=status)


@dataclass(frozen=True, slots=True)
class UnexpectedCpSatStatusError(Exception):
    status: int

    @override
    def __str__(self) -> str:
        return f"unexpected CP-SAT status: {self.status}"


def _solve(model: cp_model.CpModel, problem: PlanningProblem) -> tuple[cp_model.CpSolver, int]:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = problem.settings.time_limit_seconds
    solver.parameters.num_search_workers = SOLVER_WORKER_COUNT
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
    problem: PlanningProblem, candidates: tuple[Candidate, ...]
) -> PartialRouteDiagnostic:
    artifacts = build_route_model(problem, candidates, require_required_coverage=False)
    solver, status = _solve(artifacts.model, problem)
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
            )
        case cp_model.INFEASIBLE | cp_model.UNKNOWN:
            return PartialRouteDiagnostic(
                unmet_required_requirement_keys=tuple(sorted(problem.required_requirement_keys)),
                scheduled_steps=(),
            )
        case _:
            raise UnexpectedCpSatStatusError(status=status)

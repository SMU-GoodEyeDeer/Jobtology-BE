from dataclasses import dataclass
from typing import assert_never

from ortools.sat.python import cp_model

from jobtology_be.planning.candidate_models import Candidate, KnownKrwCost, UnknownKrwCost
from jobtology_be.planning.solver_models import (
    CandidateAvailability,
    InvalidPlanningProblemError,
    PlanningProblem,
    PlanningSlot,
    microseconds_from_planning_start,
    objective_snapshot,
)

NORMALIZED_SCORE_MAXIMUM = 10_000


@dataclass(frozen=True, slots=True)
class RouteModelArtifacts:
    model: cp_model.CpModel
    candidates: tuple[Candidate, ...]
    selected_by_action: dict[str, cp_model.IntVar]
    assignments_by_action: dict[str, tuple[cp_model.IntVar, ...]]
    covered_by_requirement: dict[str, cp_model.IntVar]


def build_route_model(
    problem: PlanningProblem,
    candidates: tuple[Candidate, ...],
    *,
    require_required_coverage: bool,
) -> RouteModelArtifacts:
    _validate_candidates(candidates)
    model = cp_model.CpModel()
    availability = {item.action_id: item for item in problem.candidate_availability}
    selected_by_action = {
        candidate.action_id: model.new_bool_var(f"selected_{candidate.action_id}")
        for candidate in candidates
    }
    assignments_by_action = {
        candidate.action_id: tuple(
            model.new_bool_var(f"assigned_{candidate.action_id}_{slot_index}")
            for slot_index in range(len(problem.calendar))
        )
        for candidate in candidates
    }
    completion_by_action = {
        candidate.action_id: model.new_int_var(
            0,
            objective_snapshot(problem).horizon_microseconds,
            f"completion_{candidate.action_id}",
        )
        for candidate in candidates
    }
    for candidate in candidates:
        assignments = assignments_by_action[candidate.action_id]
        model.add(sum(assignments) == candidate.estimated_hours * selected_by_action[candidate.action_id])
        for slot_index, assignment in enumerate(assignments):
            if not _is_available(problem.calendar[slot_index], availability.get(candidate.action_id)):
                model.add(assignment == 0)
        model.add_max_equality(
            completion_by_action[candidate.action_id],
            [
                microseconds_from_planning_start(problem, slot.ends_at) * assignments[slot_index]
                for slot_index, slot in enumerate(problem.calendar)
            ],
        )
        if problem.constraints.max_out_of_pocket_krw is not None:
            match candidate.cost:
                case UnknownKrwCost():
                    model.add(selected_by_action[candidate.action_id] == 0)
                case KnownKrwCost():
                    pass
                case unreachable:
                    assert_never(unreachable)
    for slot_index in range(len(problem.calendar)):
        model.add(sum(assignments_by_action[candidate.action_id][slot_index] for candidate in candidates) <= 1)
    for week_key in sorted({slot.capacity_week_key for slot in problem.calendar}):
        model.add(
            sum(
                assignments_by_action[candidate.action_id][slot_index]
                for candidate in candidates
                for slot_index, slot in enumerate(problem.calendar)
                if slot.capacity_week_key == week_key
            )
            <= problem.constraints.available_hours_per_week
        )
    for candidate in candidates:
        for prerequisite_action_id in candidate.prerequisite_action_ids:
            model.add(selected_by_action[candidate.action_id] <= selected_by_action[prerequisite_action_id])
            for slot_index, assignment in enumerate(assignments_by_action[candidate.action_id]):
                model.add(
                    completion_by_action[prerequisite_action_id]
                    <= microseconds_from_planning_start(problem, problem.calendar[slot_index].starts_at)
                ).only_enforce_if(assignment)
    if problem.constraints.max_out_of_pocket_krw is not None:
        model.add(
            sum(
                _known_cost(candidate) * selected_by_action[candidate.action_id]
                for candidate in candidates
            )
            <= problem.constraints.max_out_of_pocket_krw
        )
    covered_by_requirement = _add_requirement_coverage(
        model,
        problem,
        candidates,
        selected_by_action,
        require_required_coverage=require_required_coverage,
    )
    _add_objective(
        model,
        problem,
        candidates,
        selected_by_action,
        covered_by_requirement,
        completion_by_action,
    )
    return RouteModelArtifacts(
        model=model,
        candidates=candidates,
        selected_by_action=selected_by_action,
        assignments_by_action=assignments_by_action,
        covered_by_requirement=covered_by_requirement,
    )


def _validate_candidates(candidates: tuple[Candidate, ...]) -> None:
    action_ids = tuple(candidate.action_id for candidate in candidates)
    if len(set(action_ids)) != len(action_ids):
        raise InvalidPlanningProblemError(reason="candidate action IDs must be unique")
    available_actions = frozenset(action_ids)
    for candidate in candidates:
        if candidate.estimated_hours <= 0:
            raise InvalidPlanningProblemError(reason="candidate effort must be positive")
        if set(candidate.prerequisite_action_ids) - available_actions:
            raise InvalidPlanningProblemError(reason="candidate prerequisites must be included")


def _is_available(slot: PlanningSlot, availability: CandidateAvailability | None) -> bool:
    if availability is None:
        return True
    return availability.available_from <= slot.starts_at and slot.ends_at <= availability.available_until


def _known_cost(candidate: Candidate) -> int:
    match candidate.cost:
        case KnownKrwCost(krw=krw):
            return krw
        case UnknownKrwCost():
            return 0
        case unreachable:
            assert_never(unreachable)


def _add_requirement_coverage(
    model: cp_model.CpModel,
    problem: PlanningProblem,
    candidates: tuple[Candidate, ...],
    selected_by_action: dict[str, cp_model.IntVar],
    *,
    require_required_coverage: bool,
) -> dict[str, cp_model.IntVar]:
    covered_by_requirement: dict[str, cp_model.IntVar] = {}
    for requirement_key in sorted(
        problem.required_requirement_keys | problem.preferred_requirement_keys
    ):
        covered = model.new_bool_var(f"covered_{requirement_key}")
        matching = [
            selected_by_action[candidate.action_id]
            for candidate in candidates
            if requirement_key in candidate.outcome_requirement_keys
        ]
        if matching:
            model.add_max_equality(covered, matching)
        else:
            model.add(covered == 0)
        if require_required_coverage and requirement_key in problem.required_requirement_keys:
            model.add(covered == 1)
        covered_by_requirement[requirement_key] = covered
    return covered_by_requirement


def _add_objective(
    model: cp_model.CpModel,
    problem: PlanningProblem,
    candidates: tuple[Candidate, ...],
    selected_by_action: dict[str, cp_model.IntVar],
    covered_by_requirement: dict[str, cp_model.IntVar],
    completion_by_action: dict[str, cp_model.IntVar],
) -> None:
    objective = objective_snapshot(problem)
    completion = model.new_int_var(0, objective.horizon_microseconds, "route_completion")
    if completion_by_action:
        model.add_max_equality(completion, list(completion_by_action.values()))
    else:
        model.add(completion == 0)
    coverage_points = _coverage_points(model, problem, covered_by_requirement)
    time_remaining = model.new_int_var(0, objective.horizon_microseconds, "time_remaining")
    model.add(time_remaining == objective.horizon_microseconds - completion)
    time_points = model.new_int_var(0, NORMALIZED_SCORE_MAXIMUM, "time_points")
    model.add_division_equality(
        time_points,
        time_remaining * NORMALIZED_SCORE_MAXIMUM,
        objective.horizon_microseconds,
    )
    cost_points = _cost_points(model, candidates, selected_by_action, objective)
    model.maximize(
        objective.coverage_weight * coverage_points
        + objective.time_weight * time_points
        + objective.cost_weight * cost_points
    )


def _coverage_points(
    model: cp_model.CpModel,
    problem: PlanningProblem,
    covered_by_requirement: dict[str, cp_model.IntVar],
) -> cp_model.IntVar:
    preferred_count = len(problem.preferred_requirement_keys)
    points = model.new_int_var(0, NORMALIZED_SCORE_MAXIMUM, "coverage_points")
    if preferred_count == 0:
        model.add(points == 0)
        return points
    covered = sum(covered_by_requirement[key] for key in problem.preferred_requirement_keys)
    model.add_division_equality(points, covered * NORMALIZED_SCORE_MAXIMUM, preferred_count)
    return points


def _cost_points(
    model: cp_model.CpModel,
    candidates: tuple[Candidate, ...],
    selected_by_action: dict[str, cp_model.IntVar],
    objective,
) -> cp_model.IntVar:
    points = model.new_int_var(0, NORMALIZED_SCORE_MAXIMUM, "cost_points")
    if objective.known_cost_upper_bound_krw == 0:
        model.add(points == 0)
        return points
    known_spend = sum(
        _known_cost(candidate) * selected_by_action[candidate.action_id] for candidate in candidates
    )
    cost_credit = model.new_int_var(0, objective.known_cost_upper_bound_krw, "cost_credit")
    unknown_selected = _unknown_selected(model, candidates, selected_by_action)
    model.add(cost_credit == objective.known_cost_upper_bound_krw - known_spend).only_enforce_if(
        ~unknown_selected
    )
    model.add(cost_credit == 0).only_enforce_if(unknown_selected)
    model.add_division_equality(
        points,
        cost_credit * NORMALIZED_SCORE_MAXIMUM,
        objective.known_cost_upper_bound_krw,
    )
    return points


def _unknown_selected(
    model: cp_model.CpModel,
    candidates: tuple[Candidate, ...],
    selected_by_action: dict[str, cp_model.IntVar],
) -> cp_model.IntVar:
    selected_unknown = [
        selected_by_action[candidate.action_id]
        for candidate in candidates
        if isinstance(candidate.cost, UnknownKrwCost)
    ]
    unknown_selected = model.new_bool_var("unknown_cost_selected")
    if selected_unknown:
        model.add_max_equality(unknown_selected, selected_unknown)
    else:
        model.add(unknown_selected == 0)
    return unknown_selected

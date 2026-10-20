from collections import Counter
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
PARTIAL_REQUIRED_COVERAGE_WEIGHT = 100 * NORMALIZED_SCORE_MAXIMUM + 1


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
        available_capacity = sum(min(count, problem.constraints.available_hours_per_week)
                                 for count in Counter(slot.capacity_week_key for slot in problem.calendar
                                                      if _is_available(slot, availability.get(candidate.action_id))).values())
        if candidate.estimated_hours > available_capacity:
            model.add(selected_by_action[candidate.action_id] == 0)
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
        assignments_by_action,
        reward_required_coverage=not require_required_coverage,
    )
    _add_schedule_hint(
        model, problem, candidates, selected_by_action, assignments_by_action,
        require_required_coverage=require_required_coverage,
    )
    return RouteModelArtifacts(
        model=model,
        candidates=candidates,
        selected_by_action=selected_by_action,
        assignments_by_action=assignments_by_action,
        covered_by_requirement=covered_by_requirement,
    )


def _add_schedule_hint(
    model: cp_model.CpModel,
    problem: PlanningProblem,
    candidates: tuple[Candidate, ...],
    selected_by_action: dict[str, cp_model.IntVar],
    assignments_by_action: dict[str, tuple[cp_model.IntVar, ...]],
    *,
    require_required_coverage: bool,
) -> None:
    """Seed search with a valid list schedule; hints never constrain the optimum."""
    available = {item.action_id: item for item in problem.candidate_availability}
    allocations: dict[str, tuple[int, ...]] = {}
    used: set[int] = set()
    weekly: dict[str, int] = {}
    spent = 0
    pending = sorted(candidates, key=lambda item: (
        item.action_id not in problem.foundational_priority_action_ids, item.action_id,
    ))
    while pending:
        ready = next((item for item in pending if all(
            key in allocations for key in item.prerequisite_action_ids
        )), None)
        if ready is None:
            break
        pending.remove(ready)
        cap = problem.constraints.max_out_of_pocket_krw
        if cap is not None and (isinstance(ready.cost, UnknownKrwCost)
                                or spent + _known_cost(ready) > cap):
            continue
        earliest = max((allocations[key][-1] + 1 for key in ready.prerequisite_action_ids), default=0)
        chosen: list[int] = []
        chosen_weekly: dict[str, int] = {}
        for index in range(earliest, len(problem.calendar)):
            slot = problem.calendar[index]
            week = slot.capacity_week_key
            if (index in used or not _is_available(slot, available.get(ready.action_id))
                    or weekly.get(week, 0) + chosen_weekly.get(week, 0)
                    >= problem.constraints.available_hours_per_week):
                continue
            chosen.append(index)
            chosen_weekly[week] = chosen_weekly.get(week, 0) + 1
            if len(chosen) == ready.estimated_hours:
                break
        if len(chosen) != ready.estimated_hours:
            continue
        allocations[ready.action_id] = tuple(chosen)
        used.update(chosen)
        spent += _known_cost(ready)
        for week, count in chosen_weekly.items():
            weekly[week] = weekly.get(week, 0) + count
    covered = frozenset(key for item in candidates if item.action_id in allocations
                        for key in item.outcome_requirement_keys)
    if require_required_coverage and not problem.required_requirement_keys <= covered:
        return
    for item in candidates:
        selected = set(allocations.get(item.action_id, ()))
        model.add_hint(selected_by_action[item.action_id], int(bool(selected)))
        for index, assignment in enumerate(assignments_by_action[item.action_id]):
            model.add_hint(assignment, int(index in selected))


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
    assignments_by_action: dict[str, tuple[cp_model.IntVar, ...]],
    *,
    reward_required_coverage: bool,
) -> None:
    objective = objective_snapshot(problem)
    completion = model.new_int_var(0, objective.horizon_microseconds, "route_completion")
    if completion_by_action:
        model.add_max_equality(completion, list(completion_by_action.values()))
    else:
        model.add(completion == 0)
    # Selected effort cannot finish before that many capacity slots have elapsed.
    # This redundant bound removes the need to discover it by permuting thousands
    # of interchangeable hourly assignments over a long planning horizon.
    total_hours = model.new_int_var(0, len(problem.calendar), 'total_selected_hours')
    model.add(total_hours == sum(
        candidate.estimated_hours * selected_by_action[candidate.action_id]
        for candidate in candidates
    ))
    earliest_completion = model.new_int_var(
        0, objective.horizon_microseconds, 'earliest_capacity_completion',
    )
    model.add_element(total_hours, [0, *(
        microseconds_from_planning_start(problem, slot.ends_at) for slot in problem.calendar
    )], earliest_completion)
    model.add(completion >= earliest_completion)
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
    weighted_points = (
        objective.coverage_weight * coverage_points
        + objective.time_weight * time_points
        + objective.cost_weight * cost_points
    )
    primary_score = weighted_points
    if reward_required_coverage:
        # One more required requirement outweighs the entire normalized weighted score.
        required_covered = sum(
            covered_by_requirement[key] for key in sorted(problem.required_requirement_keys)
        )
        primary_score = PARTIAL_REQUIRED_COVERAGE_WEIGHT * required_covered + primary_score
    foundations = tuple(candidate for candidate in candidates
                        if candidate.action_id in problem.foundational_priority_action_ids)
    if not foundations:
        model.maximize(primary_score)
        return
    # A bounded tie-break prefers earlier foundational hours only after coverage, completion,
    # and cost. This is a preference, so real prerequisites and availability still take precedence.
    slot_count = len(problem.calendar)
    maximum = slot_count * sum(candidate.estimated_hours for candidate in foundations)
    early_foundation_hours = sum(
        (slot_count - slot_index) * assignment for candidate in foundations
        for slot_index, assignment in enumerate(assignments_by_action[candidate.action_id])
    )
    foundation_points = model.new_int_var(0, NORMALIZED_SCORE_MAXIMUM, 'foundation_priority_points')
    model.add_division_equality(
        foundation_points, early_foundation_hours * NORMALIZED_SCORE_MAXIMUM, maximum,
    )
    model.maximize(primary_score * (NORMALIZED_SCORE_MAXIMUM + 1) + foundation_points)


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

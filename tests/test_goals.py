from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from jobtology_be.modules.goals.validation import (
    GoalPlanningInput,
    GoalValidationPolicy,
    InvalidMinimumLeadTimeError,
    NaiveReferenceTimeError,
    TargetByTooSoonError,
    UnknownTimezoneError,
    validate_goal_planning_input,
)
from jobtology_be.planning.contracts import PlanningConstraints

REFERENCE_TIME = datetime(2026, 9, 22, 9, tzinfo=UTC)
MINIMUM_LEAD_TIME = timedelta(days=7)


def test_validate_goal_planning_input_returns_typed_deadline_and_existing_constraints() -> None:
    # Given
    original_time_phrase = "October 1 at 9 AM Korea time"
    request = GoalPlanningInput(
        target_by=datetime(2026, 10, 1, 9, tzinfo=ZoneInfo("Asia/Seoul")),
        timezone="Asia/Seoul",
        original_time_phrase=original_time_phrase,
        available_hours_per_week=12,
        budget_mode="LOW_COST",
        max_out_of_pocket_krw=None,
    )

    # When
    validated = validate_goal_planning_input(
        request,
        reference_time=REFERENCE_TIME,
        policy=GoalValidationPolicy(minimum_lead_time=MINIMUM_LEAD_TIME),
    )

    # Then
    assert validated.deadline.timezone == ZoneInfo("Asia/Seoul")
    assert validated.deadline.original_time_phrase == original_time_phrase
    assert isinstance(validated.constraints, PlanningConstraints)
    assert validated.constraints.available_hours_per_week == 12
    assert validated.constraints.budget_mode == "LOW_COST"
    assert validated.constraints.max_out_of_pocket_krw is None


def test_validate_goal_planning_input_rejects_target_at_minimum_lead_time_boundary() -> None:
    # Given
    request = GoalPlanningInput(
        target_by=REFERENCE_TIME + MINIMUM_LEAD_TIME,
        timezone="UTC",
        original_time_phrase="one week from now",
        available_hours_per_week=1,
    )

    # When / Then
    with pytest.raises(TargetByTooSoonError):
        _ = validate_goal_planning_input(
            request,
            reference_time=REFERENCE_TIME,
            policy=GoalValidationPolicy(minimum_lead_time=MINIMUM_LEAD_TIME),
        )


def test_validate_goal_planning_input_accepts_target_after_minimum_lead_time_boundary() -> None:
    # Given
    target_by = REFERENCE_TIME + MINIMUM_LEAD_TIME + timedelta(microseconds=1)
    request = GoalPlanningInput(
        target_by=target_by,
        timezone="UTC",
        original_time_phrase="just after the lead time",
        available_hours_per_week=60,
    )

    # When
    validated = validate_goal_planning_input(
        request,
        reference_time=REFERENCE_TIME,
        policy=GoalValidationPolicy(minimum_lead_time=MINIMUM_LEAD_TIME),
    )

    # Then
    assert validated.deadline.target_by == target_by
    assert validated.constraints.available_hours_per_week == 60


def test_validate_goal_planning_input_measures_minimum_lead_time_as_elapsed_time_across_fold() -> None:
    # Given
    new_york = ZoneInfo("America/New_York")
    reference_time = datetime(2026, 11, 1, 1, 30, tzinfo=new_york, fold=0)
    target_by = datetime(2026, 11, 1, 1, 30, tzinfo=new_york, fold=1)
    request = GoalPlanningInput(
        target_by=target_by,
        timezone="America/New_York",
        original_time_phrase="the second 1:30 AM after daylight saving time ends",
        available_hours_per_week=12,
    )

    # When
    validated = validate_goal_planning_input(
        request,
        reference_time=reference_time,
        policy=GoalValidationPolicy(minimum_lead_time=timedelta(minutes=30)),
    )

    # Then
    assert validated.deadline.target_by.fold == 1
    assert validated.deadline.target_by.utcoffset() == timedelta(hours=-5)


def test_goal_validation_policy_rejects_negative_minimum_lead_time() -> None:
    # Given / When / Then
    with pytest.raises(InvalidMinimumLeadTimeError):
        _ = GoalValidationPolicy(minimum_lead_time=-timedelta(microseconds=1))


def test_goal_planning_input_rejects_naive_target_time() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match="timezone_aware"):
        _ = GoalPlanningInput(
            target_by=datetime.fromisoformat("2026-10-01T09:00:00"),
            timezone="Asia/Seoul",
            original_time_phrase="October 1 at 9 AM",
            available_hours_per_week=12,
        )


def test_validate_goal_planning_input_rejects_naive_reference_time() -> None:
    # Given
    request = GoalPlanningInput(
        target_by=datetime(2026, 10, 1, 9, tzinfo=UTC),
        timezone="UTC",
        original_time_phrase="October 1",
        available_hours_per_week=12,
    )

    # When / Then
    with pytest.raises(NaiveReferenceTimeError):
        _ = validate_goal_planning_input(
            request,
            reference_time=datetime.fromisoformat("2026-09-22T09:00:00"),
            policy=GoalValidationPolicy(minimum_lead_time=MINIMUM_LEAD_TIME),
        )


def test_validate_goal_planning_input_rejects_unknown_iana_timezone() -> None:
    # Given
    request = GoalPlanningInput(
        target_by=datetime(2026, 10, 1, 9, tzinfo=UTC),
        timezone="Korea/Seoul",
        original_time_phrase="October 1",
        available_hours_per_week=12,
    )

    # When / Then
    with pytest.raises(UnknownTimezoneError):
        _ = validate_goal_planning_input(
            request,
            reference_time=REFERENCE_TIME,
            policy=GoalValidationPolicy(minimum_lead_time=MINIMUM_LEAD_TIME),
        )


@pytest.mark.parametrize("weekly_hours", [12.5, True])
def test_goal_planning_input_rejects_non_integer_weekly_capacity(
    weekly_hours: float | bool,
) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        _ = GoalPlanningInput.model_validate(
            {
                "target_by": datetime(2026, 10, 1, 9, tzinfo=UTC),
                "timezone": "UTC",
                "original_time_phrase": "October 1",
                "available_hours_per_week": weekly_hours,
            }
        )


@pytest.mark.parametrize("weekly_hours", [0, 61])
def test_validate_goal_planning_input_reuses_weekly_capacity_bounds(
    weekly_hours: int,
) -> None:
    # Given
    request = GoalPlanningInput(
        target_by=datetime(2026, 10, 1, 9, tzinfo=UTC),
        timezone="UTC",
        original_time_phrase="October 1",
        available_hours_per_week=weekly_hours,
    )

    # When / Then
    with pytest.raises(ValidationError):
        _ = validate_goal_planning_input(
            request,
            reference_time=REFERENCE_TIME,
            policy=GoalValidationPolicy(minimum_lead_time=MINIMUM_LEAD_TIME),
        )


def test_validate_goal_planning_input_reuses_nonnegative_hard_budget_cap() -> None:
    # Given
    request = GoalPlanningInput(
        target_by=datetime(2026, 10, 1, 9, tzinfo=UTC),
        timezone="UTC",
        original_time_phrase="October 1",
        available_hours_per_week=12,
        max_out_of_pocket_krw=-1,
    )

    # When / Then
    with pytest.raises(ValidationError, match="greater_than_equal"):
        _ = validate_goal_planning_input(
            request,
            reference_time=REFERENCE_TIME,
            policy=GoalValidationPolicy(minimum_lead_time=MINIMUM_LEAD_TIME),
        )


def test_goal_planning_input_keeps_budget_preference_separate_from_hard_cap() -> None:
    # Given
    request = GoalPlanningInput(
        target_by=datetime(2026, 10, 1, 9, tzinfo=UTC),
        timezone="UTC",
        original_time_phrase="October 1",
        available_hours_per_week=12,
        budget_mode="REGULAR",
        max_out_of_pocket_krw=50_000,
    )

    # When
    validated = validate_goal_planning_input(
        request,
        reference_time=REFERENCE_TIME,
        policy=GoalValidationPolicy(minimum_lead_time=MINIMUM_LEAD_TIME),
    )

    # Then
    assert validated.constraints.budget_mode == "REGULAR"
    assert validated.constraints.max_out_of_pocket_krw == 50_000

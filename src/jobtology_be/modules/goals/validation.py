from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Literal, override
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, ConfigDict, Field, StrictInt, StrictStr

from jobtology_be.contracts import Contract
from jobtology_be.planning.contracts import PlanningConstraints


@dataclass(frozen=True, slots=True)
class GoalPlanningValidationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class InvalidMinimumLeadTimeError(GoalPlanningValidationError):
    minimum_lead_time: timedelta

    @override
    def __str__(self) -> str:
        return "minimum lead time must not be negative"


@dataclass(frozen=True, slots=True)
class NaiveReferenceTimeError(GoalPlanningValidationError):
    @override
    def __str__(self) -> str:
        return "reference time must include a timezone"


@dataclass(frozen=True, slots=True)
class TargetByTooSoonError(GoalPlanningValidationError):
    minimum_lead_time: timedelta

    @override
    def __str__(self) -> str:
        return "target deadline must be after the configured minimum lead time"


@dataclass(frozen=True, slots=True)
class UnknownTimezoneError(GoalPlanningValidationError):
    timezone: str

    @override
    def __str__(self) -> str:
        return f"unknown IANA timezone: {self.timezone}"


@dataclass(frozen=True, slots=True)
class GoalValidationPolicy:
    minimum_lead_time: timedelta

    def __post_init__(self) -> None:
        if self.minimum_lead_time < timedelta():
            raise InvalidMinimumLeadTimeError(minimum_lead_time=self.minimum_lead_time)


class GoalPlanningInput(Contract):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    target_by: AwareDatetime
    timezone: StrictStr = Field(min_length=1, max_length=100)
    original_time_phrase: StrictStr = Field(min_length=1, max_length=500)
    available_hours_per_week: StrictInt
    budget_mode: Literal["REGULAR", "LOW_COST"] = "REGULAR"
    max_out_of_pocket_krw: StrictInt | None = None
    fastest_path: bool = False
    needs_portfolio: bool = False
    career_switch: bool = False


@dataclass(frozen=True, slots=True)
class ValidatedGoalDeadline:
    target_by: datetime
    timezone: ZoneInfo
    original_time_phrase: str


@dataclass(frozen=True, slots=True)
class ValidatedGoalPlanningInput:
    deadline: ValidatedGoalDeadline
    constraints: PlanningConstraints


def validate_goal_planning_input(
    request: GoalPlanningInput,
    *,
    reference_time: datetime,
    policy: GoalValidationPolicy,
) -> ValidatedGoalPlanningInput:
    constraints = PlanningConstraints(
        target_by=request.target_by,
        available_hours_per_week=request.available_hours_per_week,
        budget_mode=request.budget_mode,
        max_out_of_pocket_krw=request.max_out_of_pocket_krw,
        fastest_path=request.fastest_path,
        needs_portfolio=request.needs_portfolio,
        career_switch=request.career_switch,
    )
    if reference_time.tzinfo is None or reference_time.utcoffset() is None:
        raise NaiveReferenceTimeError()
    try:
        timezone = ZoneInfo(request.timezone)
    except (ValueError, ZoneInfoNotFoundError):
        raise UnknownTimezoneError(timezone=request.timezone) from None

    target_by_utc = request.target_by.astimezone(UTC)
    minimum_target_by_utc = reference_time.astimezone(UTC) + policy.minimum_lead_time
    if target_by_utc <= minimum_target_by_utc:
        raise TargetByTooSoonError(minimum_lead_time=policy.minimum_lead_time)
    return ValidatedGoalPlanningInput(
        deadline=ValidatedGoalDeadline(
            target_by=request.target_by,
            timezone=timezone,
            original_time_phrase=request.original_time_phrase,
        ),
        constraints=constraints,
    )

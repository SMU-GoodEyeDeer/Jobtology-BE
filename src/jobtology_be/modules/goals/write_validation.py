"""Validation shared by the goal API and service before any state is written."""

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jobtology_be.planning.calendar import MAX_FLEXIBLE_CALENDAR_HORIZON


@dataclass(frozen=True, slots=True)
class GoalWriteValidationError(Exception):
    field: str
    code: str
    message: str


def validate_goal_schedule(
    target_by: datetime, timezone: str, *, now: datetime | None = None,
) -> None:
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise GoalWriteValidationError(
            field="timezone", code="unknown_timezone",
            message="Choose a valid IANA timezone, such as Asia/Seoul or UTC.",
        ) from None
    if target_by.tzinfo is None or target_by.utcoffset() is None:
        raise GoalWriteValidationError(
            field="target_by", code="timezone_required",
            message="Include a timezone offset in the goal deadline.",
        )
    reference = datetime.now(UTC) if now is None else now.astimezone(UTC)
    elapsed = target_by.astimezone(UTC) - reference
    if elapsed.total_seconds() <= 0:
        raise GoalWriteValidationError(
            field="target_by", code="deadline_not_future",
            message="Choose a goal deadline in the future.",
        )
    if elapsed > MAX_FLEXIBLE_CALENDAR_HORIZON:
        raise GoalWriteValidationError(
            field="target_by", code="deadline_out_of_range",
            message="Choose a goal deadline within the next 366 days.",
        )

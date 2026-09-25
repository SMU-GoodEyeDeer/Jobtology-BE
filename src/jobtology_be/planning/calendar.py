from datetime import datetime, timedelta

from jobtology_be.planning.solver_models import SLOT_DURATION, PlanningSlot

MAX_FLEXIBLE_CALENDAR_HORIZON = timedelta(days=366)


class FlexibleCalendarError(Exception):
    pass


def flexible_weekly_slots(
    planning_started_at: datetime,
    target_by: datetime,
    available_hours_per_week: int,
) -> tuple[PlanningSlot, ...]:
    if not _is_aware(planning_started_at) or not _is_aware(target_by):
        raise FlexibleCalendarError("calendar timestamps must be timezone-aware")
    if available_hours_per_week < 1:
        raise FlexibleCalendarError("weekly availability must be positive")
    if target_by <= planning_started_at:
        return ()
    if target_by - planning_started_at > MAX_FLEXIBLE_CALENDAR_HORIZON:
        raise FlexibleCalendarError("planning horizon exceeds the flexible calendar limit")

    slot_start = _next_hour(planning_started_at)
    slots: list[PlanningSlot] = []
    allocated_by_week: dict[tuple[int, int], int] = {}
    while slot_start + SLOT_DURATION <= target_by:
        iso_year, iso_week, _ = slot_start.isocalendar()
        week = (iso_year, iso_week)
        allocated = allocated_by_week.get(week, 0)
        if allocated < available_hours_per_week:
            slots.append(
                PlanningSlot(
                    starts_at=slot_start,
                    ends_at=slot_start + SLOT_DURATION,
                    capacity_week_key=f"{iso_year}-W{iso_week:02d}",
                )
            )
            allocated_by_week[week] = allocated + 1
        slot_start += SLOT_DURATION
    return tuple(slots)


def _next_hour(value: datetime) -> datetime:
    rounded = value.replace(minute=0, second=0, microsecond=0)
    return rounded if rounded == value else rounded + SLOT_DURATION


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

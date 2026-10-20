from collections.abc import Collection, Sequence
from datetime import UTC, date, datetime, timedelta, tzinfo

from jobtology_be.contracts import WeeklyAvailabilityWindow
from jobtology_be.planning.solver_models import SLOT_DURATION, PlanningSlot

MAX_FLEXIBLE_CALENDAR_HORIZON = timedelta(days=366)


class FlexibleCalendarError(Exception):
    pass


def flexible_weekly_slots(
    planning_started_at: datetime,
    target_by: datetime,
    available_hours_per_week: int,
    weekly_availability: Sequence[WeeklyAvailabilityWindow] = (),
    unavailable_dates: Sequence[date] = (),
) -> tuple[PlanningSlot, ...]:
    if not _is_aware(planning_started_at) or not _is_aware(target_by):
        raise FlexibleCalendarError("calendar timestamps must be timezone-aware")
    if available_hours_per_week < 1:
        raise FlexibleCalendarError("weekly availability must be positive")
    started_utc = planning_started_at.astimezone(UTC)
    target_utc = target_by.astimezone(UTC)
    if target_utc <= started_utc:
        return ()
    if target_utc - started_utc > MAX_FLEXIBLE_CALENDAR_HORIZON:
        raise FlexibleCalendarError("planning horizon exceeds the flexible calendar limit")

    slot_start = _next_hour(planning_started_at)
    slots: list[PlanningSlot] = []
    allocated_by_week: dict[tuple[int, int], int] = {}
    excluded = frozenset(unavailable_dates)
    while slot_start + SLOT_DURATION <= target_utc:
        # Advance real hours in UTC, retaining the goal's local ISO week for
        # capacity accounting. Local arithmetic creates nonexistent/repeated
        # intervals at daylight-saving transitions and breaks JSON round trips.
        iso_year, iso_week, _ = slot_start.astimezone(planning_started_at.tzinfo).isocalendar()
        week = (iso_year, iso_week)
        allocated = allocated_by_week.get(week, 0)
        if allocated < available_hours_per_week and slot_is_available(
            slot_start, slot_start + SLOT_DURATION, weekly_availability,
            excluded, planning_started_at.tzinfo,
        ):
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


def slot_is_available(
    starts_at: datetime,
    ends_at: datetime,
    weekly_availability: Sequence[WeeklyAvailabilityWindow] = (),
    unavailable_dates: Collection[date] = (),
    timezone: tzinfo | None = None,
) -> bool:
    """Check occupied real hours against local windows, including DST folds/gaps."""
    if not _is_aware(starts_at) or not _is_aware(ends_at):
        raise FlexibleCalendarError("calendar timestamps must be timezone-aware")
    start = starts_at.astimezone(UTC)
    end = ends_at.astimezone(UTC)
    if end <= start or end - start > MAX_FLEXIBLE_CALENDAR_HORIZON:
        return False
    zone = timezone or starts_at.tzinfo
    excluded = (unavailable_dates if isinstance(unavailable_dates, frozenset)
                else frozenset(unavailable_dates))
    cursor = start
    while cursor < end:
        segment_end = min(cursor + SLOT_DURATION, end)
        local_start = cursor.astimezone(zone)
        local_tail = (segment_end - timedelta(microseconds=1)).astimezone(zone)
        if local_start.date() in excluded or local_tail.date() in excluded:
            return False
        if weekly_availability and not all(
            any(window.weekday == point.isoweekday()
                and window.start_hour <= point.hour < window.end_hour
                for window in weekly_availability)
            for point in (local_start, local_tail)
        ):
            return False
        cursor = segment_end
    return True


def _next_hour(value: datetime) -> datetime:
    rounded = value.replace(minute=0, second=0, microsecond=0).astimezone(UTC)
    return rounded if rounded == value.astimezone(UTC) else rounded + SLOT_DURATION


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

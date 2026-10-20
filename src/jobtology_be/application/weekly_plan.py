"""Project persisted solver schedules and explicitly labelled weekly customizations."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import ceil
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from jobtology_be.application.services.roadmaps import StepState
from jobtology_be.application.weekly_plan_models import (
    PlanContext,
    PlanOverrides,
    PlanStepInput,
    WeeklyPlanAllocation,
    WeeklyPlanResponse,
    WeeklyPlanStep,
    WeeklyPlanWarning,
)

_STATE: Final = TypeAdapter[StepState](StepState)
# Keep custom plans manageable without turning priority order into a prerequisite chain.
MAX_ACTIVE_LEARNING_ACTIVITIES: Final = 2


def _identity(item: PlanStepInput) -> tuple[str | None, str]:
    outcome = item.step.outcomes[0] if item.step.outcomes else None
    key = outcome.get('requirement_key') if isinstance(outcome, Mapping) else None
    name = outcome.get('raw_text') if isinstance(outcome, Mapping) else None
    return (
        key if isinstance(key, str) else None,
        name if isinstance(name, str) and name else item.step.title or item.step.action_id,
    )


def _hours(context: PlanContext, item: PlanStepInput) -> int:
    """The pinned proposal, rather than current catalog metadata, owns its effort."""
    pinned = context.step_hours.get(item.step.step_key)
    if pinned is not None:
        return pinned
    meta = item.metadata
    return meta.estimated_hours if meta and meta.estimated_hours is not None else 20


def _reason(item: PlanStepInput, by_id: Mapping[UUID, PlanStepInput]) -> str:
    demand = item.metadata.demand_pct if item.metadata else None
    suffix = f' · 공고 {demand}%가 요구해요' if demand is not None and demand > 0 else ''
    previous = tuple(by_id[key] for key in item.step.prerequisite_step_ids if key in by_id)
    if previous:
        names = '·'.join(_identity(step)[1] for step in previous[:2])
        return f'{names} 완료 후 진행하는 단계예요' + suffix
    return '저장된 로드맵 순서로 배치했어요' + suffix


@dataclass(frozen=True, slots=True)
class _Scheduled:
    starts_at: datetime
    ends_at: datetime
    allocations: tuple[WeeklyPlanAllocation, ...]


def _working_hour_offset(
    value: datetime, starts_at: datetime, weekly: int, timezone: ZoneInfo,
) -> int:
    """Locate a solver start on the same flexible weekly capacity axis as planning."""
    first = starts_at.astimezone(UTC)
    rounded = first.replace(minute=0, second=0, microsecond=0)
    first = rounded if first == rounded else rounded + timedelta(hours=1)
    local_start, local_value = first.astimezone(timezone), value.astimezone(timezone)
    first_monday = local_start.date() - timedelta(days=local_start.weekday())
    value_monday = local_value.date() - timedelta(days=local_value.weekday())
    weeks = (value_monday - first_monday).days // 7
    if weeks <= 0:
        return max(0, min(weekly, ceil((value.astimezone(UTC) - first).total_seconds() / 3600)))
    next_week = datetime.combine(first_monday + timedelta(weeks=1), datetime.min.time(), timezone)
    first_capacity = min(weekly, max(0, int((next_week.astimezone(UTC) - first).total_seconds() // 3600)))
    value_week_start = datetime.combine(value_monday, datetime.min.time(), timezone).astimezone(UTC)
    value_week_hours = min(weekly, max(0, ceil((value.astimezone(UTC) - value_week_start).total_seconds() / 3600)))
    return first_capacity + (weeks - 1) * weekly + value_week_hours


def _preferred_start_offsets(
    context: PlanContext, ordered: tuple[PlanStepInput, ...], overrides: PlanOverrides | None,
    starts_at: datetime, timezone: ZoneInfo,
) -> dict[UUID, int]:
    recommended = tuple(sorted(ordered, key=lambda item: (item.step.position, str(item.step.step_id))))
    if overrides is not None and ordered != recommended:
        return {}
    return {
        item.step.step_id: _working_hour_offset(
            item.step.planned_start, starts_at, context.recommended_weekly_hours, timezone,
        )
        for item in ordered if _has_solver_dates(item) and item.step.planned_start is not None
    }


def _pack(
    context: PlanContext,
    ordered: tuple[PlanStepInput, ...],
    overrides: PlanOverrides | None,
    starts_at: datetime,
    start: date,
    timezone: ZoneInfo,
    fixed: Mapping[UUID, _Scheduled] | None = None,
) -> dict[UUID, _Scheduled]:
    """Stagger ready activities in a small rolling window, sharing real hourly slots.

    The order is a priority among eligible activities, rather than a new prerequisite
    chain. At most two activities are in progress, so short activities do not stretch
    over the whole plan. Source start spacing is a soft preference when order is
    unchanged; it never leaves available capacity idle when no activity is active.
    """
    weekly = overrides.weekly_hours if overrides else context.recommended_weekly_hours
    rests = frozenset(overrides.rest_weeks) if overrides else frozenset()
    by_id = {item.step.step_id: item for item in ordered}
    fixed = fixed or {}
    remaining = {item.step.step_id: overrides.hours.get(str(item.step.step_id), _hours(context, item))
                 if overrides else _hours(context, item) for item in ordered
                 if item.step.step_id not in fixed}
    first: dict[UUID, datetime] = {}
    last: dict[UUID, datetime] = {}
    allocations: dict[UUID, dict[int, int]] = {key: {} for key in remaining}
    preferred_starts = _preferred_start_offsets(context, ordered, overrides, starts_at, timezone)
    reschedule_at = overrides.saved_at if overrides and overrides.saved_at else context.as_of
    reschedule_future = bool(fixed) or (overrides is not None and context.as_of is not None)
    future_start = max(starts_at, reschedule_at or starts_at,
                       max((item.ends_at for item in fixed.values()), default=starts_at)) if reschedule_future else starts_at
    cursor = future_start.astimezone(UTC)
    rounded = cursor.replace(minute=0, second=0, microsecond=0)
    cursor = rounded if rounded == cursor else rounded + timedelta(hours=1)
    used_by_week: dict[int, int] = {}
    for schedule in fixed.values():
        for allocation in schedule.allocations:
            used_by_week[allocation.week] = used_by_week.get(allocation.week, 0) + allocation.hours
    positions = {item.step.step_id: index for index, item in enumerate(ordered)}
    waiting = {item.step.step_id: {
        key for key in item.step.prerequisite_step_ids
        if key not in by_id or by_id[key].step.state != 'COMPLETED'
    } for item in ordered if item.step.step_id in remaining}
    dependents: dict[UUID, set[UUID]] = {}
    for key, prerequisites in waiting.items():
        for prerequisite in prerequisites:
            dependents.setdefault(prerequisite, set()).add(key)
    ready = deque(key for key in remaining if not waiting[key])
    active: deque[UUID] = deque()
    hours_left = sum(remaining.values())
    total_hours = hours_left
    while hours_left:
        local = cursor.astimezone(timezone)
        week = (local.date() - start).days // 7 + 1
        if week in rests or used_by_week.get(week, 0) >= weekly:
            next_monday = start + timedelta(weeks=week)
            cursor = datetime.combine(next_monday, datetime.min.time(), timezone).astimezone(UTC)
            continue
        admitted: list[UUID] = []
        while ready and len(active) + len(admitted) < MAX_ACTIVE_LEARNING_ACTIVITIES:
            next_ready = next((key for key in ready
                               if preferred_starts.get(key, 0) <= total_hours - hours_left), None)
            if next_ready is None and (active or admitted):
                break
            next_ready = next_ready or ready[0]
            ready.remove(next_ready)
            admitted.append(next_ready)
        # Give a newly opened activity its first hour now, rather than shifting a
        # preserved source start by another turn through the current activity.
        active.extendleft(reversed(admitted))
        # Broken/missing source dependencies must remain visible and produce warnings,
        # rather than hanging the scheduler or silently dropping activities.
        if not active:
            active.append(next(key for key, hours in remaining.items() if hours))
        key = active.popleft()
        end = cursor + timedelta(hours=1)
        first.setdefault(key, cursor)
        last[key] = end
        allocations[key][week] = allocations[key].get(week, 0) + 1
        remaining[key] -= 1
        hours_left -= 1
        if remaining[key]:
            active.append(key)
        else:
            for dependent in dependents.get(key, ()):
                waiting[dependent].discard(key)
                if (not waiting[dependent] and remaining[dependent]
                        and dependent not in ready and dependent not in active):
                    ready.append(dependent)
            ready = deque(sorted(ready, key=positions.__getitem__))
        used_by_week[week] = used_by_week.get(week, 0) + 1
        cursor = end
    return {**fixed, **{key: _Scheduled(first[key], last[key], tuple(
        WeeklyPlanAllocation(week=week, hours=hours) for week, hours in weeks.items()
    )) for key, weeks in allocations.items()}}


def _has_solver_dates(item: PlanStepInput) -> bool:
    step = item.step
    return (step.planned_start is not None and step.planned_end is not None
            and step.planned_end >= step.planned_start)


def _dates(
    item: PlanStepInput, packed: Mapping[UUID, _Scheduled],
    timezone: ZoneInfo, *, use_solver: bool,
) -> tuple[date, date]:
    step = item.step
    if use_solver and _has_solver_dates(item):
        assert step.planned_start is not None and step.planned_end is not None
        return (step.planned_start.astimezone(timezone).date(),
                _last_occupied_date(step.planned_start, step.planned_end, timezone))
    schedule = packed[step.step_id]
    return (schedule.starts_at.astimezone(timezone).date(),
            _last_occupied_date(schedule.starts_at, schedule.ends_at, timezone))


def _unmet(
    item: PlanStepInput, by_id: Mapping[UUID, PlanStepInput], schedules: Mapping[UUID, _Scheduled],
    *, use_solver: bool,
) -> tuple[UUID, ...]:
    unmet: list[UUID] = []
    for prerequisite_id in item.step.prerequisite_step_ids:
        prerequisite = by_id.get(prerequisite_id)
        if prerequisite is None:
            unmet.append(prerequisite_id)
        elif prerequisite.step.state == 'COMPLETED':
            continue
        elif use_solver and _has_solver_dates(item) and _has_solver_dates(prerequisite):
            assert item.step.planned_start is not None and prerequisite.step.planned_end is not None
            if prerequisite.step.planned_end > item.step.planned_start:
                unmet.append(prerequisite_id)
        elif schedules[prerequisite_id].ends_at > schedules[item.step.step_id].starts_at:
            unmet.append(prerequisite_id)
    return tuple(unmet)


def build_weekly_plan(
    context: PlanContext, steps: tuple[PlanStepInput, ...], overrides: PlanOverrides | None = None,
) -> WeeklyPlanResponse:
    """Retain immutable solver dates and progress; validate customizations explicitly."""
    timezone = ZoneInfo(context.timezone)
    version = overrides.version if overrides else 0
    snapshots = overrides.step_schedules if overrides else {}
    if overrides is not None and not overrides.is_customized:
        overrides = None
    recommended = tuple(sorted(steps, key=lambda item: (item.step.position, str(item.step.step_id))))
    by_id = {item.step.step_id: item for item in recommended}
    custom_order = tuple(UUID(key) for key in overrides.order) if overrides else ()
    ordered_ids = tuple(key for key in custom_order if key in by_id)
    ordered_ids += tuple(item.step.step_id for item in recommended if item.step.step_id not in ordered_ids)
    ordered = tuple(by_id[key] for key in ordered_ids) if overrides else recommended
    if (overrides is not None and overrides.weekly_hours == context.recommended_weekly_hours
            and not overrides.rest_weeks and ordered == recommended
            and all(overrides.hours.get(str(item.step.step_id), _hours(context, item))
                    == _hours(context, item) for item in recommended)):
        overrides = None
    recommended_positions = {item.step.step_id: i for i, item in enumerate(recommended, 1)}
    weekly = overrides.weekly_hours if overrides else context.recommended_weekly_hours
    rests = tuple(sorted(overrides.rest_weeks)) if overrides else ()
    solver_starts = tuple(item.step.planned_start for item in recommended
                          if _has_solver_dates(item) and item.step.planned_start is not None)
    starts_at = min(solver_starts, default=context.created_at)
    start = starts_at.astimezone(timezone).date()
    start -= timedelta(days=start.weekday())
    missing_dates = tuple(item.step.step_id for item in recommended if not _has_solver_dates(item))
    recommended_packed = _pack(context, recommended, None, starts_at, start, timezone) if missing_dates else {}
    fixed: dict[UUID, _Scheduled] = {}
    for item in recommended:
        if item.step.state != 'COMPLETED':
            continue
        snapshot = snapshots.get(str(item.step.step_id))
        if snapshot is not None:
            fixed[item.step.step_id] = _Scheduled(
                snapshot.planned_start_at or datetime.combine(snapshot.start_date, datetime.min.time(), timezone),
                snapshot.planned_end_at or datetime.combine(snapshot.end_date + timedelta(days=1), datetime.min.time(), timezone),
                snapshot.week_allocations,
            )
        elif _has_solver_dates(item):
            assert item.step.planned_start is not None and item.step.planned_end is not None
            fixed[item.step.step_id] = _Scheduled(
                item.step.planned_start, item.step.planned_end, _exact_allocations(context, item, start, timezone),
            )
        else:
            fixed[item.step.step_id] = recommended_packed[item.step.step_id]
    packed = (_pack(context, ordered, overrides, starts_at, start, timezone, fixed=fixed)
              if overrides else {**recommended_packed, **fixed})
    recommended_dates = tuple(_dates(item, recommended_packed, timezone, use_solver=True)
                              for item in recommended)
    warnings: list[WeeklyPlanWarning] = []
    if missing_dates:
        warnings.append(WeeklyPlanWarning(
            code='MISSING_SOLVER_SCHEDULE',
            message='일부 단계의 원본 일정이 없어 주간 학습량으로 추정했어요. 원본 일정의 제약 충족 여부는 확인할 수 없어요.',
            step_ids=missing_dates,
        ))
    result: list[WeeklyPlanStep] = []
    for number, item in enumerate(ordered, 1):
        meta = item.metadata
        level = meta.ncs_level if meta else None
        recommended_hours = _hours(context, item)
        snapshot = snapshots.get(str(item.step.step_id)) if item.step.state == 'COMPLETED' else None
        hours = (snapshot.hours if snapshot is not None else
                 overrides.hours.get(str(item.step.step_id), recommended_hours) if overrides else recommended_hours)
        if item.step.step_id in fixed:
            schedule = fixed[item.step.step_id]
            first_date, last_date = (schedule.starts_at.astimezone(timezone).date(),
                                     _last_occupied_date(schedule.starts_at, schedule.ends_at, timezone))
        else:
            first_date, last_date = _dates(item, packed, timezone, use_solver=overrides is None)
        unmet = () if item.step.state == 'COMPLETED' else _unmet(item, by_id, packed, use_solver=overrides is None)
        if unmet:
            warnings.append(WeeklyPlanWarning(
                code='PREREQUISITE_VIOLATION',
                message=f'{_identity(item)[1]} 단계가 필요한 선행 단계보다 먼저 배치됐어요. 순서를 조정해 주세요.',
                step_ids=(item.step.step_id,), prerequisite_step_ids=unmet,
            ))
        if item.step.step_id in packed:
            exact_start, exact_end = packed[item.step.step_id].starts_at, packed[item.step.step_id].ends_at
        else:
            exact_start, exact_end = item.step.planned_start, item.step.planned_end
        key, name = _identity(item)
        basis = meta.hours_basis if meta and meta.hours_basis else 'ESTIMATED'
        if meta and meta.estimated_hours is not None and recommended_hours != meta.estimated_hours:
            # The proposal may model remaining effort from partial competency credit.
            # The source's official total is not an official remaining-hours estimate.
            basis = 'ESTIMATED'
        result.append(WeeklyPlanStep(
            step_id=item.step.step_id, number=number,
            recommended_number=recommended_positions[item.step.step_id],
            title=item.step.title or item.step.action_id, name=name, requirement_key=key,
            state=_STATE.validate_python(item.step.state),
            phase='BASIC' if level is not None and level <= 3 else
                  'ADVANCED' if level is not None and level >= 6 else 'CORE',
            ncs_level=level, field_code=meta.field_code if meta else None,
            field_name=meta.field_name if meta else None,
            hours=hours, recommended_hours=recommended_hours,
            hours_basis='CUSTOM' if hours != recommended_hours else basis,
            demand_pct=meta.demand_pct if meta else None,
            start_week=(first_date - start).days // 7 + 1,
            end_week=(last_date - start).days // 7 + 1,
            start_date=first_date, end_date=last_date,
            reason=_reason(item, by_id), moved=number != recommended_positions[item.step.step_id],
            prerequisite_step_ids=item.step.prerequisite_step_ids,
            unmet_prerequisite_step_ids=unmet,
            completion_criteria=tuple(value for value in item.step.criteria if isinstance(value, str)),
            planned_start_at=exact_start, planned_end_at=exact_end,
            week_allocations=(snapshot.week_allocations if snapshot is not None else
                packed[item.step.step_id].allocations if overrides is not None or not _has_solver_dates(item)
                else _exact_allocations(context, item, start, timezone)),
        ))
    completion = max((step.end_date for step in result), default=start)
    days_past_target = max(0, (completion - context.target_date).days) if context.target_date else 0
    if days_past_target:
        warnings.append(WeeklyPlanWarning(
            code='TARGET_DATE_EXCEEDED',
            message=f'예상 완료일 {completion.isoformat()}은 목표일 {context.target_date}보다 '
                    f'{days_past_target}일 늦어요. 이 일정은 목표 기한을 충족하지 않아요.',
        ))
    return WeeklyPlanResponse(
        version=version, roadmap_id=context.roadmap_id, start_date=start, target_date=context.target_date,
        weekly_hours=weekly, recommended_weekly_hours=context.recommended_weekly_hours,
        rest_weeks=rests, is_customized=overrides is not None,
        saved_at=overrides.saved_at.astimezone(timezone) if overrides and overrides.saved_at else None,
        total_hours=sum(step.hours for step in result),
        total_weeks=max((step.end_week for step in result), default=0),
        completion_date=completion,
        recommended_completion_date=max((last for _, last in recommended_dates), default=start),
        schedule_source='CUSTOM' if overrides else 'FALLBACK' if missing_dates else 'SOLVER',
        deadline_exceeded=bool(days_past_target), days_past_target=days_past_target,
        has_dependency_violations=any(step.unmet_prerequisite_step_ids for step in result),
        warnings=tuple(warnings), steps=tuple(result),
    )


def _exact_allocations(context: PlanContext, item: PlanStepInput, start: date,
                       timezone: ZoneInfo) -> tuple[WeeklyPlanAllocation, ...]:
    counts: dict[int, int] = {}
    for value in context.step_allocations.get(item.step.step_key, ()):
        week = (value.astimezone(timezone).date() - start).days // 7 + 1
        counts[week] = counts.get(week, 0) + 1
    return tuple(WeeklyPlanAllocation(week=week, hours=hours) for week, hours in sorted(counts.items()))


def _last_occupied_date(starts_at: datetime, ends_at: datetime, timezone: ZoneInfo) -> date:
    # Solver and packer intervals are [start, end). Midnight belongs to the next
    # interval, not an extra learning day/week in the user-facing projection.
    occupied = ends_at - timedelta(microseconds=1) if ends_at > starts_at else ends_at
    return occupied.astimezone(timezone).date()

"""Project persisted solver schedules and explicitly labelled weekly customizations."""

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from jobtology_be.application.services.roadmaps import StepState
from jobtology_be.application.weekly_plan_models import (
    PlanContext,
    PlanOverrides,
    PlanStepInput,
    WeeklyPlanResponse,
    WeeklyPlanStep,
    WeeklyPlanWarning,
)

_STATE: Final = TypeAdapter[StepState](StepState)


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


def _pack(
    context: PlanContext,
    ordered: tuple[PlanStepInput, ...],
    overrides: PlanOverrides | None,
) -> dict[UUID, tuple[int, int]]:
    """Keep the user's effort/rest-week layout without changing the saved solver schedule."""
    weekly = overrides.weekly_hours if overrides else context.recommended_weekly_hours
    rests = frozenset(overrides.rest_weeks) if overrides else frozenset()
    week, used = 1, 0
    packed_steps: dict[UUID, tuple[int, int]] = {}
    for item in ordered:
        remaining = (overrides.hours.get(str(item.step.step_id), _hours(context, item))
                     if overrides else _hours(context, item))
        while week in rests or used == weekly:
            week, used = week + 1, 0
        first = week
        while remaining:
            while week in rests or used == weekly:
                week, used = week + 1, 0
            packed = min(remaining, weekly - used)
            remaining -= packed
            used += packed
        packed_steps[item.step.step_id] = (first, week)
    return packed_steps


def _has_solver_dates(item: PlanStepInput) -> bool:
    step = item.step
    return (step.planned_start is not None and step.planned_end is not None
            and step.planned_end >= step.planned_start)


def _dates(
    item: PlanStepInput, start: date, packed: Mapping[UUID, tuple[int, int]],
    timezone: ZoneInfo, *, use_solver: bool,
) -> tuple[date, date]:
    step = item.step
    if use_solver and _has_solver_dates(item):
        assert step.planned_start is not None and step.planned_end is not None
        return step.planned_start.astimezone(timezone).date(), step.planned_end.astimezone(timezone).date()
    first, last = packed[step.step_id]
    return start + timedelta(weeks=first - 1), start + timedelta(weeks=last - 1, days=6)


def _unmet(
    item: PlanStepInput, by_id: Mapping[UUID, PlanStepInput], positions: Mapping[UUID, int],
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
        elif positions[prerequisite_id] >= positions[item.step.step_id]:
            unmet.append(prerequisite_id)
    return tuple(unmet)


def build_weekly_plan(
    context: PlanContext, steps: tuple[PlanStepInput, ...], overrides: PlanOverrides | None = None,
) -> WeeklyPlanResponse:
    """Retain immutable solver dates and progress; validate customizations explicitly."""
    timezone = ZoneInfo(context.timezone)
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
    positions = {item.step.step_id: i for i, item in enumerate(ordered, 1)}
    recommended_positions = {item.step.step_id: i for i, item in enumerate(recommended, 1)}
    weekly = overrides.weekly_hours if overrides else context.recommended_weekly_hours
    rests = tuple(sorted(overrides.rest_weeks)) if overrides else ()
    solver_starts = tuple(item.step.planned_start for item in recommended
                          if _has_solver_dates(item) and item.step.planned_start is not None)
    start = min(solver_starts, default=context.created_at).astimezone(timezone).date()
    start -= timedelta(days=start.weekday())
    packed = _pack(context, ordered, overrides)
    recommended_packed = _pack(context, recommended, None)
    recommended_dates = tuple(_dates(item, start, recommended_packed, timezone, use_solver=True)
                              for item in recommended)
    missing_dates = tuple(item.step.step_id for item in recommended if not _has_solver_dates(item))
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
        hours = overrides.hours.get(str(item.step.step_id), recommended_hours) if overrides else recommended_hours
        first_date, last_date = _dates(item, start, packed, timezone, use_solver=overrides is None)
        unmet = _unmet(item, by_id, positions, use_solver=overrides is None)
        if unmet:
            warnings.append(WeeklyPlanWarning(
                code='PREREQUISITE_VIOLATION',
                message=f'{_identity(item)[1]} 단계가 필요한 선행 단계보다 먼저 배치됐어요. 순서를 조정해 주세요.',
                step_ids=(item.step.step_id,), prerequisite_step_ids=unmet,
            ))
        key, name = _identity(item)
        basis = meta.hours_basis if meta and meta.hours_basis else 'ESTIMATED'
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
        roadmap_id=context.roadmap_id, start_date=start, target_date=context.target_date,
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

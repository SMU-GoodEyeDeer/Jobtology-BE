"""Pure weekly recommendation and continuous-hour scheduling."""

from collections.abc import Mapping
from datetime import timedelta
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from jobtology_be.application.services.roadmaps import StepState
from jobtology_be.application.weekly_plan_models import (
    PlanContext,
    PlanOverrides,
    PlanStepInput,
    WeeklyPlanResponse,
    WeeklyPlanStep,
)

SEOUL: Final = ZoneInfo('Asia/Seoul')
_STATE: Final = TypeAdapter[StepState](StepState)


def _identity(item: PlanStepInput) -> tuple[str | None, str]:
    outcome = item.step.outcomes[0] if item.step.outcomes else None
    key = outcome.get('requirement_key') if isinstance(outcome, Mapping) else None
    name = outcome.get('raw_text') if isinstance(outcome, Mapping) else None
    return (
        key if isinstance(key, str) else None,
        name if isinstance(name, str) and name else item.step.title or item.step.action_id,
    )


def _sort_key(item: PlanStepInput) -> tuple[bool, int, int, str]:
    meta = item.metadata
    level = meta.ncs_level if meta else None
    return (level is None, level or 0, -(meta.demand_pct or 0) if meta else 0,
            (meta.ncs_base_code if meta else None) or item.step.action_id)


def _prerequisites(item: PlanStepInput, ordered: tuple[PlanStepInput, ...]) -> tuple[PlanStepInput, ...]:
    meta = item.metadata
    if meta is None or meta.field_code is None or meta.ncs_level is None:
        return ()
    return tuple(other for other in ordered if other.metadata is not None
                 and other.metadata.field_code == meta.field_code
                 and other.metadata.ncs_level is not None
                 and other.metadata.ncs_level < meta.ncs_level)


def _reason(item: PlanStepInput, ordered: tuple[PlanStepInput, ...]) -> str:
    meta = item.metadata
    demand = meta.demand_pct if meta else None
    suffix = f' · 공고 {demand}%가 요구해요' if demand is not None and demand > 0 else ''
    if meta is None or meta.ncs_level is None:
        return '수준 정보가 없어 기본 학습 순서로 배치했어요' + suffix
    field = (meta.field_name or '해당').removesuffix('엔지니어링')
    previous = _prerequisites(item, ordered)
    first_basic = next((s for s in ordered if s.metadata is not None
                        and s.metadata.ncs_level is not None and s.metadata.ncs_level <= 3), None)
    if meta.ncs_level <= 3 and item == first_basic:
        reason = f'{meta.ncs_level}수준 기초 역량이라 먼저 해요'
    elif not previous:
        peer = next((s for s in ordered[:ordered.index(item)] if s.metadata is not None
                     and s.metadata.field_code == meta.field_code
                     and s.metadata.ncs_level == meta.ncs_level), None)
        reason = (f'{_identity(peer)[1]}에 이어지는 {field} {meta.ncs_level}수준 단계예요'
                  if peer is not None and meta.field_code is not None
                  else f'{field} 분야의 첫 단계({meta.ncs_level}수준)예요')
    else:
        lower = max(s.metadata.ncs_level for s in previous
                    if s.metadata is not None and s.metadata.ncs_level is not None)
        names = '·'.join([_identity(s)[1] for s in previous
                          if s.metadata is not None and s.metadata.ncs_level == lower][:2])
        reason = f'{names} 다음 {field} {meta.ncs_level}수준 단계예요'
    return reason + suffix


def build_weekly_plan(
    context: PlanContext, steps: tuple[PlanStepInput, ...], overrides: PlanOverrides | None = None,
) -> WeeklyPlanResponse:
    """Compute from immutable inputs only; schedule every step regardless of state."""
    recommended = tuple(sorted(steps, key=_sort_key))
    by_id = {str(s.step.step_id): s for s in recommended}
    ordered = tuple(by_id[key] for key in overrides.order) if overrides else recommended
    positions = {item.step.step_id: i for i, item in enumerate(ordered, 1)}
    recommended_positions = {item.step.step_id: i for i, item in enumerate(recommended, 1)}
    weekly = overrides.weekly_hours if overrides else context.recommended_weekly_hours
    rests = tuple(sorted(overrides.rest_weeks)) if overrides else ()
    start = context.created_at.astimezone(SEOUL).date()
    start -= timedelta(days=start.weekday())
    week, used = 1, 0
    result: list[WeeklyPlanStep] = []
    recommended_total = 0
    for number, item in enumerate(ordered, 1):
        meta = item.metadata
        level = meta.ncs_level if meta else None
        recommended_hours = meta.estimated_hours if meta and meta.estimated_hours is not None else 20
        recommended_total += recommended_hours
        hours = overrides.hours.get(str(item.step.step_id), recommended_hours) if overrides else recommended_hours
        remaining = hours
        while week in rests or used == weekly:
            week, used = week + 1, 0
        first = week
        while remaining:
            while week in rests or used == weekly:
                week, used = week + 1, 0
            packed = min(remaining, weekly - used)
            remaining -= packed
            used += packed
        prerequisites = _prerequisites(item, recommended)
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
            start_week=first, end_week=week,
            start_date=start + timedelta(weeks=first - 1),
            end_date=start + timedelta(weeks=week - 1, days=6),
            reason=_reason(item, recommended), moved=number != recommended_positions[item.step.step_id],
            prerequisite_step_ids=tuple(s.step.step_id for s in prerequisites),
            unmet_prerequisite_step_ids=tuple(s.step.step_id for s in prerequisites
                                             if positions[s.step.step_id] > number),
        ))
    total_weeks = result[-1].end_week if result else 0
    recommended_weeks = (recommended_total + context.recommended_weekly_hours - 1) // context.recommended_weekly_hours
    return WeeklyPlanResponse(
        roadmap_id=context.roadmap_id, start_date=start, target_date=context.target_date,
        weekly_hours=weekly, recommended_weekly_hours=context.recommended_weekly_hours,
        rest_weeks=rests, is_customized=overrides is not None,
        saved_at=overrides.saved_at.astimezone(SEOUL) if overrides else None,
        total_hours=sum(s.hours for s in result), total_weeks=total_weeks,
        completion_date=start + timedelta(weeks=total_weeks - 1, days=6),
        recommended_completion_date=start + timedelta(weeks=recommended_weeks - 1, days=6),
        steps=tuple(result),
    )

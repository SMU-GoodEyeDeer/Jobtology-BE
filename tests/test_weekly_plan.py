from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from jobtology_be.application import weekly_plan
from jobtology_be.application.queries import RoadmapStepView
from jobtology_be.application.requirement_metadata import RequirementMetadata


def production_steps():
    rows = (
        ('2001020233', '개발자 환경 구축', 2, 15, 0, '20010202', '응용SW엔지니어링'),
        ('2001020234', '개발 환경 운영 지원', 2, 20, 0, '20010202', '응용SW엔지니어링'),
        ('2001020415', 'SQL작성', 2, 30, 0, '20010204', 'DB엔지니어링'),
        ('2001020405', '데이터베이스 구현', 4, 20, 40, '20010204', 'DB엔지니어링'),
        ('2001020409', '데이터 표준화', 4, 24, 0, '20010204', 'DB엔지니어링'),
        ('2001020412', '데이터 전환', 5, 20, 10, '20010204', 'DB엔지니어링'),
        ('2001020201', '요구사항 확인', 5, 40, 0, '20010202', '응용SW엔지니어링'),
        ('2001020235', '자료구조 활용', 5, 20, 0, '20010202', '응용SW엔지니어링'),
        ('2001020406', '데이터 품질관리', 5, 20, 0, '20010204', 'DB엔지니어링'),
        ('2001020414', 'SQL응용', 5, 30, 0, '20010204', 'DB엔지니어링'),
        ('2001020219', '애플리케이션 요구사항 분석', 7, 20, 10, '20010202', '응용SW엔지니어링'),
    )
    return tuple(weekly_plan.PlanStepInput(
        step=RoadmapStepView(
            step_id=UUID(int=i), step_key=code, position=i, action_id=f'study:{code}',
            template_revision=1, state='TODO', planned_start=None, planned_end=None,
            outcomes=({'requirement_key': f'BACKEND_DEVELOPER:{code}', 'raw_text': name},),
            criteria=(), prerequisite_step_ids=(), title=f'{name} 학습',
        ),
        metadata=RequirementMetadata(
            ncs_level=level, demand_pct=demand, estimated_hours=hours, hours_basis='OFFICIAL',
            ncs_base_code=code, field_code=field, field_name=field_name,
        ),
    ) for i, (code, name, level, hours, demand, field, field_name) in enumerate(rows, 1))


def context():
    return weekly_plan.PlanContext(
        roadmap_id=UUID(int=100), created_at=datetime(2026, 10, 18, 15, tzinfo=UTC),
        recommended_weekly_hours=10, target_date=None,
    )


def test_production_recommended_order_and_weeks():
    # Given
    steps = production_steps()
    # When
    plan = weekly_plan.build_weekly_plan(context(), tuple(reversed(steps)))
    # Then
    assert [step.step_id.int for step in plan.steps] == list(range(1, 12))
    assert [(s.start_week, s.end_week) for s in plan.steps] == [
        (1, 2), (2, 4), (4, 7), (7, 9), (9, 11), (11, 13),
        (13, 17), (17, 19), (19, 21), (21, 24), (24, 26),
    ]
    assert plan.start_date == date(2026, 10, 19)
    assert plan.completion_date == date(2027, 4, 18)
    assert plan.total_hours == 259
    assert plan.total_weeks == 26
    assert plan.saved_at is None
    assert not plan.is_customized


def test_custom_hours_and_rest_weeks_pack_continuously():
    # Given
    overrides = weekly_plan.PlanOverrides(
        weekly_hours=12, rest_weeks=(10, 11),
        order=tuple(str(UUID(int=i)) for i in range(1, 12)),
        hours={str(UUID(int=10)): 24}, saved_at=datetime(2026, 10, 18, tzinfo=UTC),
    )
    # When
    plan = weekly_plan.build_weekly_plan(context(), production_steps(), overrides)
    # Then
    assert plan.total_hours == 253
    assert plan.total_weeks == 24
    assert plan.completion_date == date(2027, 4, 4)
    assert plan.recommended_completion_date == date(2027, 4, 18)
    assert plan.steps[9].hours_basis == 'CUSTOM'
    assert all(s.start_week not in (10, 11) and s.end_week not in (10, 11) for s in plan.steps)


def test_moving_sql_application_before_sql_writing_reports_unmet_prerequisite():
    # Given
    order = (10, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11)
    overrides = weekly_plan.PlanOverrides(
        weekly_hours=10, rest_weeks=(), order=tuple(str(UUID(int=i)) for i in order),
        hours={}, saved_at=datetime(2026, 10, 18, tzinfo=UTC),
    )
    # When
    plan = weekly_plan.build_weekly_plan(context(), production_steps(), overrides)
    # Then
    assert UUID(int=3) in plan.steps[0].unmet_prerequisite_step_ids
    assert plan.steps[0].moved
    assert plan.steps[0].recommended_number == 10


def test_null_metadata_uses_fallbacks_without_inventing_fields():
    # Given
    original = production_steps()[0].step
    step = weekly_plan.PlanStepInput(step=original, metadata=None)
    # When
    plan = weekly_plan.build_weekly_plan(context(), (step,))
    # Then
    result = plan.steps[0]
    assert (result.ncs_level, result.field_code, result.field_name, result.demand_pct) == (None,) * 4
    assert (result.hours, result.recommended_hours, result.hours_basis) == (20, 20, 'ESTIMATED')
    assert result.phase == 'CORE'
    assert result.prerequisite_step_ids == ()


@pytest.mark.parametrize('weekly_hours,hours,weeks', [(10, 20, (1, 2)), (10, 10, (1, 1)), (1, 20, (1, 20))])
def test_exact_capacity_uses_last_hour_not_next_week(weekly_hours, hours, weeks):
    # Given
    overrides = weekly_plan.PlanOverrides(
        weekly_hours=weekly_hours, rest_weeks=(), order=(str(UUID(int=1)),),
        hours={str(UUID(int=1)): hours}, saved_at=datetime(2026, 10, 18, tzinfo=UTC),
    )
    # When
    result = weekly_plan.build_weekly_plan(context(), production_steps()[:1], overrides).steps[0]
    # Then
    assert (result.start_week, result.end_week) == weeks


def test_reasons_use_immediately_lower_field_level_and_demand():
    # Given
    steps = production_steps()
    # When
    result = weekly_plan.build_weekly_plan(context(), steps).steps
    # Then
    assert result[0].reason == '2수준 기초 역량이라 먼저 해요'
    assert result[1].reason == '응용SW 분야의 첫 단계(2수준)예요'
    assert result[2].reason == 'DB 분야의 첫 단계(2수준)예요'
    assert result[3].reason == 'SQL작성 다음 DB 4수준 단계예요 · 공고 40%가 요구해요'
    assert result[5].reason == '데이터베이스 구현·데이터 표준화 다음 DB 5수준 단계예요 · 공고 10%가 요구해요'
    assert result[10].reason == '요구사항 확인·자료구조 활용 다음 응용SW 7수준 단계예요 · 공고 10%가 요구해요'
    assert [s.phase for s in result] == ['BASIC'] * 3 + ['CORE'] * 7 + ['ADVANCED']
    assert result[3].prerequisite_step_ids == (UUID(int=3),)
    assert result[10].field_name == '응용SW엔지니어링'


def test_rest_week_one_and_boundary_rest_do_not_consume_capacity():
    # Given
    overrides = weekly_plan.PlanOverrides(
        weekly_hours=10, rest_weeks=(1, 3, 104),
        order=(str(UUID(int=1)), str(UUID(int=2))), hours={str(UUID(int=1)): 10},
        saved_at=datetime(2026, 10, 18, tzinfo=UTC),
    )
    # When
    result = weekly_plan.build_weekly_plan(context(), production_steps()[:2], overrides)
    # Then
    assert [(s.start_week, s.end_week) for s in result.steps] == [(2, 2), (4, 5)]
    assert result.steps[0].start_date == date(2026, 10, 26)
    assert result.steps[1].end_date == date(2026, 11, 22)
    assert result.total_weeks == 5


def test_null_levels_sort_last_and_unknown_fields_are_not_shared_prerequisites():
    # Given
    steps = production_steps()[:3]
    inputs = tuple(replace(s, metadata=replace(s.metadata, ncs_level=None, field_code=None,
                                              demand_pct=None)) for s in steps)
    inputs = (replace(inputs[0], metadata=replace(inputs[0].metadata, ncs_level=2)), *inputs[1:])
    # When
    result = weekly_plan.build_weekly_plan(context(), tuple(reversed(inputs)))
    # Then
    assert [s.step_id.int for s in result.steps] == [1, 2, 3]
    assert all(s.prerequisite_step_ids == () for s in result.steps)


def test_name_and_requirement_fall_back_when_outcomes_are_empty():
    # Given
    step = replace(production_steps()[0].step, outcomes=(), state='COMPLETED')
    # When
    result = weekly_plan.build_weekly_plan(context(), (weekly_plan.PlanStepInput(step, None),))
    # Then
    assert result.steps[0].requirement_key is None
    assert result.steps[0].name == step.title
    assert result.steps[0].state == 'COMPLETED'
    assert result.total_hours == 20


def test_step_boundaries_reuse_capacity_without_rounding_each_step():
    # Given
    steps = production_steps()[:4]
    inputs = []
    for item, hours in zip(steps, (2, 9, 3, 16), strict=True):
        assert item.metadata is not None
        inputs.append(replace(item, metadata=replace(item.metadata, estimated_hours=hours)))
    # When
    result = weekly_plan.build_weekly_plan(context(), tuple(inputs))
    # Then
    assert [(s.start_week, s.end_week) for s in result.steps] == [(1, 1), (1, 2), (2, 2), (2, 3)]
    assert result.total_weeks == 3
    assert result.completion_date == result.recommended_completion_date == date(2026, 11, 8)


def test_unknown_level_reason_still_appends_known_demand():
    # Given
    item = production_steps()[0]
    assert item.metadata is not None
    item = replace(item, metadata=replace(item.metadata, ncs_level=None, demand_pct=40))
    # When
    result = weekly_plan.build_weekly_plan(context(), (item,))
    # Then
    assert result.steps[0].reason.endswith(' · 공고 40%가 요구해요')

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
    inputs = list(production_steps())
    inputs[9] = replace(inputs[9], step=replace(inputs[9].step, prerequisite_step_ids=(UUID(int=3),)))
    plan = weekly_plan.build_weekly_plan(context(), tuple(inputs), overrides)
    # Then
    assert UUID(int=3) in plan.steps[0].unmet_prerequisite_step_ids
    assert plan.steps[0].moved
    assert plan.steps[0].recommended_number == 10
    assert plan.has_dependency_violations
    warning = next(w for w in plan.warnings if w.code == 'PREREQUISITE_VIOLATION')
    assert warning.step_ids == (UUID(int=10),)
    assert warning.prerequisite_step_ids == (UUID(int=3),)


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


def test_reasons_use_only_real_prerequisites_and_keep_demand_as_context():
    # Given
    steps = list(production_steps())
    steps[3] = replace(steps[3], step=replace(steps[3].step, prerequisite_step_ids=(UUID(int=3),)))
    # When
    result = weekly_plan.build_weekly_plan(context(), tuple(steps)).steps
    # Then
    assert result[0].reason == '저장된 로드맵 순서로 배치했어요'
    assert result[1].reason == '저장된 로드맵 순서로 배치했어요'
    assert result[2].reason == '저장된 로드맵 순서로 배치했어요'
    assert result[3].reason == 'SQL작성 완료 후 진행하는 단계예요 · 공고 40%가 요구해요'
    assert result[5].reason == '저장된 로드맵 순서로 배치했어요 · 공고 10%가 요구해요'
    assert result[10].reason == '저장된 로드맵 순서로 배치했어요 · 공고 10%가 요구해요'
    assert [s.phase for s in result] == ['BASIC'] * 3 + ['CORE'] * 7 + ['ADVANCED']
    assert result[3].prerequisite_step_ids == (UUID(int=3),)
    assert result[5].prerequisite_step_ids == ()
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


def solver_steps():
    first, second = production_steps()[:2]
    return (
        replace(first, step=replace(
            first.step, state='COMPLETED',
            planned_start=datetime(2026, 10, 19, 1, tzinfo=UTC),
            planned_end=datetime(2026, 10, 19, 5, tzinfo=UTC),
        )),
        replace(second, step=replace(
            second.step, prerequisite_step_ids=(first.step.step_id,),
            planned_start=datetime(2026, 10, 20, 1, tzinfo=UTC),
            planned_end=datetime(2026, 10, 20, 5, tzinfo=UTC),
        )),
    )


def test_solver_schedule_is_authoritative_even_when_created_or_catalog_changes():
    inputs = solver_steps()
    pinned = replace(context(), created_at=datetime(2026, 11, 5, tzinfo=UTC),
                     target_date=date(2026, 10, 20),
                     step_hours={inputs[0].step.step_key: 4, inputs[1].step.step_key: 4})
    plan = weekly_plan.build_weekly_plan(pinned, tuple(reversed(inputs)))
    assert [(step.start_date, step.end_date) for step in plan.steps] == [
        (date(2026, 10, 19), date(2026, 10, 19)),
        (date(2026, 10, 20), date(2026, 10, 20)),
    ]
    assert plan.start_date == date(2026, 10, 19)
    assert plan.completion_date == plan.recommended_completion_date == date(2026, 10, 20)
    assert plan.steps[0].state == 'COMPLETED'
    assert plan.total_hours == 8
    assert plan.schedule_source == 'SOLVER'
    assert not plan.deadline_exceeded
    assert not plan.has_dependency_violations
    assert plan.warnings == ()


def test_roadmap_positions_are_preserved_instead_of_sorting_by_ncs_metadata():
    first, second = production_steps()[:2]
    inputs = (replace(first, metadata=replace(first.metadata, ncs_level=7)), second)
    result = weekly_plan.build_weekly_plan(context(), tuple(reversed(inputs)))
    assert [step.step_id for step in result.steps] == [first.step.step_id, second.step.step_id]
    assert all(not step.prerequisite_step_ids for step in result.steps)


def test_custom_schedule_past_target_is_allowed_with_completion_warning():
    inputs = production_steps()[:2]
    overrides = weekly_plan.PlanOverrides(
        weekly_hours=10, rest_weeks=(), order=tuple(str(item.step.step_id) for item in inputs),
        hours={str(inputs[0].step.step_id): 14}, saved_at=None,
    )
    result = weekly_plan.build_weekly_plan(replace(context(), target_date=date(2026, 10, 20)),
                                         inputs, overrides)
    assert result.completion_date == date(2026, 11, 15)
    assert result.deadline_exceeded
    assert result.days_past_target == 26
    assert result.schedule_source == 'CUSTOM'
    warning = next(w for w in result.warnings if w.code == 'TARGET_DATE_EXCEEDED')
    assert '2026-11-15' in warning.message
    assert '2026-10-20' in warning.message
    assert result.saved_at is None


def test_custom_schedule_preserves_original_solver_recommendation_and_progress():
    inputs = solver_steps()
    overrides = weekly_plan.PlanOverrides(
        weekly_hours=1, rest_weeks=(1,), order=tuple(str(item.step.step_id) for item in inputs),
        hours={str(inputs[1].step.step_id): 2}, saved_at=None,
    )
    customized = weekly_plan.build_weekly_plan(context(), inputs, overrides)
    original = weekly_plan.build_weekly_plan(context(), inputs)
    assert customized.recommended_completion_date == original.completion_date == date(2026, 10, 20)
    assert customized.steps[0].state == 'COMPLETED'
    assert customized.rest_weeks == (1,)
    assert customized.steps[1].hours == 2
    assert customized.completion_date > original.completion_date
    assert original.schedule_source == 'SOLVER'


def test_actual_solver_timestamps_detect_overlapping_unfinished_prerequisites():
    first, second = solver_steps()
    first = replace(first, step=replace(first.step, state='TODO', planned_end=second.step.planned_end))
    result = weekly_plan.build_weekly_plan(context(), (first, second))
    assert result.has_dependency_violations
    assert result.steps[1].unmet_prerequisite_step_ids == (first.step.step_id,)


def test_completed_prerequisite_does_not_block_custom_order():
    inputs = solver_steps()
    overrides = weekly_plan.PlanOverrides(
        weekly_hours=10, rest_weeks=(), order=tuple(str(item.step.step_id) for item in reversed(inputs)),
        hours={}, saved_at=None,
    )
    result = weekly_plan.build_weekly_plan(context(), inputs, overrides)
    assert not result.has_dependency_violations
    assert result.steps[0].prerequisite_step_ids == (inputs[0].step.step_id,)
    assert result.steps[0].unmet_prerequisite_step_ids == ()


def test_missing_prerequisite_is_reported_instead_of_ignored():
    _, second = solver_steps()
    result = weekly_plan.build_weekly_plan(context(), (second,))
    assert result.has_dependency_violations
    assert result.steps[0].unmet_prerequisite_step_ids == (UUID(int=1),)


def test_partial_missing_dates_retains_available_solver_dates_and_warns():
    first, second = solver_steps()
    second = replace(second, step=replace(second.step, planned_start=None, planned_end=None))
    result = weekly_plan.build_weekly_plan(context(), (first, second))
    assert result.steps[0].end_date == date(2026, 10, 19)
    assert result.schedule_source == 'FALLBACK'
    assert next(w for w in result.warnings if w.code == 'MISSING_SOLVER_SCHEDULE').step_ids == (second.step.step_id,)


def test_local_goal_timezone_is_used_for_solver_and_target_dates():
    first = replace(solver_steps()[0], step=replace(
        solver_steps()[0].step,
        planned_start=datetime(2026, 10, 19, 1, tzinfo=UTC),
        planned_end=datetime(2026, 10, 19, 5, tzinfo=UTC),
    ))
    result = weekly_plan.build_weekly_plan(
        replace(context(), timezone='America/Los_Angeles', target_date=date(2026, 10, 18)), (first,),
    )
    assert result.steps[0].start_date == result.completion_date == date(2026, 10, 18)
    assert not result.deadline_exceeded


def test_empty_plan_completion_does_not_precede_start():
    result = weekly_plan.build_weekly_plan(context(), ())
    assert result.total_weeks == result.total_hours == 0
    assert result.completion_date == result.recommended_completion_date == result.start_date


def test_no_op_overrides_do_not_rebuild_solver_schedule():
    inputs = solver_steps()
    overrides = weekly_plan.PlanOverrides(
        weekly_hours=10, rest_weeks=(), order=tuple(str(item.step.step_id) for item in inputs),
        hours={}, saved_at=datetime(2026, 10, 21, tzinfo=UTC),
    )
    result = weekly_plan.build_weekly_plan(context(), inputs, overrides)
    assert result == weekly_plan.build_weekly_plan(context(), inputs)



def test_weekly_plan_exposes_persisted_project_title_and_completion_criteria():
    item = production_steps()[0]
    criteria = ('실행 가능한 프로젝트를 만든다', 'README에 실행 예시를 정리한다')
    project = replace(item, step=replace(
        item.step, title='개발자 환경 구축 포트폴리오 프로젝트', criteria=criteria,
    ))
    step = weekly_plan.build_weekly_plan(context(), (project,)).steps[0]
    assert step.title == project.step.title
    assert step.completion_criteria == criteria
    assert step.name == '개발자 환경 구축'

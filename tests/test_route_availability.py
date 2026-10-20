from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from jobtology_be.api.preferences import RoutePreferencesUpdateRequest
from jobtology_be.contracts import WeeklyAvailabilityWindow
from jobtology_be.infrastructure.persistence.context_carryforward import (
    ContextCarryforwardInput,
    carry_forward_recompute_context,
)
from jobtology_be.modules.analyses.editorial_models import InputCompleteness
from jobtology_be.planning.calendar import (
    FlexibleCalendarError,
    flexible_weekly_slots,
    slot_is_available,
)
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.planning.solver_models import PlanningConstraintSnapshot
from jobtology_be.workers.recompute_artifacts import _constraints_snapshot

REQUEST = {'expected_profile_version': 1, 'available_hours_per_week': 4,
               'availability_source': 'SELF_REPORTED', 'budget_mode': 'REGULAR',
               'fastest_path': False, 'needs_portfolio': False, 'career_switch': False}


@pytest.mark.parametrize('extra', [
    {'weekly_availability': [{'weekday': 0, 'start_hour': 18, 'end_hour': 20}]},
    {'weekly_availability': [{'weekday': 1, 'start_hour': 20, 'end_hour': 20}]},
    {'weekly_availability': [{'weekday': True, 'start_hour': 18, 'end_hour': 20}]},
    {'weekly_availability': [{'weekday': 1, 'start_hour': 18, 'end_hour': 21},
                              {'weekday': 1, 'start_hour': 20, 'end_hour': 22}]},
    {'unavailable_dates': ['2026-02-30']}, {'unavailable_dates': ['20261020']},
    {'unavailable_dates': [0]}, {'unavailable_dates': ['2026-10-20'] * 367},
])
def test_preferences_reject_invalid_availability(extra):
    with pytest.raises(ValidationError):
        RoutePreferencesUpdateRequest.model_validate(REQUEST | extra)


def test_calendar_uses_local_windows_exclusions_and_weekly_capacity():
    zone = ZoneInfo('Asia/Seoul')
    start = datetime(2026, 10, 19, tzinfo=zone)
    windows = (WeeklyAvailabilityWindow(weekday=1, start_hour=18, end_hour=20),
               WeeklyAvailabilityWindow(weekday=3, start_hour=18, end_hour=20))
    slots = flexible_weekly_slots(start, start + timedelta(days=14), 3,
                                  windows, (date(2026, 10, 19),))
    local = [slot.starts_at.astimezone(zone) for slot in slots]
    assert [(item.date().isoformat(), item.hour) for item in local] == [
        ('2026-10-21', 18), ('2026-10-21', 19),
        ('2026-10-26', 18), ('2026-10-26', 19), ('2026-10-28', 18),
    ]
    assert all(slot.ends_at - slot.starts_at == timedelta(hours=1) for slot in slots)
    assert not slot_is_available(start.replace(hour=18), start.replace(hour=19),
                                 windows, (start.date(),), zone)
    with pytest.raises(FlexibleCalendarError):
        flexible_weekly_slots(start, start + timedelta(days=367), 3, windows)


@pytest.mark.parametrize(('month', 'day', 'start_hour', 'end_hour', 'expected'), [
    (3, 8, 1, 4, 2), (11, 1, 1, 2, 2),
])
def test_windows_preserve_real_hours_across_dst(month, day, start_hour, end_hour, expected):
    zone = ZoneInfo('America/New_York')
    start = datetime(2026, month, day, tzinfo=zone)
    window = WeeklyAvailabilityWindow(weekday=7, start_hour=start_hour, end_hour=end_hour)
    slots = flexible_weekly_slots(start, start.replace(hour=4), 20, (window,))
    assert len(slots) == expected
    assert all(slot_is_available(slot.starts_at, slot.ends_at, (window,), (), zone)
               for slot in slots)
    assert all(slot.ends_at.astimezone(UTC) - slot.starts_at.astimezone(UTC)
               == timedelta(hours=1) for slot in slots)


def test_preferences_constraints_and_snapshots_are_immutable_and_carried_forward():
    zone = ZoneInfo('Asia/Seoul')
    now = datetime(2026, 10, 19, 12, tzinfo=zone)
    user_id, goal_id = uuid4(), uuid4()
    constraints = PlanningConstraints(
        target_by=now + timedelta(days=21), available_hours_per_week=4,
        weekly_availability=[{'weekday': 1, 'start_hour': 18, 'end_hour': 20}],
        unavailable_dates=['2026-10-19'],
    )
    with pytest.raises(ValidationError):
        constraints.weekly_availability[0].end_hour = 21
    with pytest.raises(ValidationError):
        constraints.unavailable_dates = ()
    snapshot = _constraints_snapshot(PlanningConstraintSnapshot.from_constraints(constraints))
    assert snapshot['unavailable_dates'] == ('2026-10-19',)
    assert snapshot['weekly_availability'][0]['weekday'] == 1
    previous = {
        'user_id': str(user_id), 'goal_id': str(goal_id), 'profile_version': 1,
        'snapshot_selection': {'occupation_id': 'BACKEND_DEVELOPER', 'basis_version': 'v1', 'release_id': 'r1'},
        'capabilities': [], 'completeness': {'entities_complete': True},
        'constraints': constraints.model_dump(mode='json'),
        'reference_at': now.isoformat(), 'planning_started_at': now.isoformat(),
        'calendar': [], 'calendar_policy': 'FLEXIBLE_WEEKLY_V1',
    }
    changed = constraints.model_copy(update={
        'weekly_availability': (WeeklyAvailabilityWindow(weekday=2, start_hour=19, end_hour=20),),
        'unavailable_dates': (date(2026, 10, 20),),
    })
    carried = carry_forward_recompute_context(previous, ContextCarryforwardInput(
        user_id=user_id, goal_id=goal_id, goal_occupation_id='BACKEND_DEVELOPER',
        profile_version=2, goal_target_by=changed.target_by, capabilities=(),
        completeness=InputCompleteness(entities_complete=True), now=now,
        current_constraints=changed,
    ))
    assert carried['constraints'] == changed.model_dump(mode='json')
    first = datetime.fromisoformat(carried['calendar'][0]['starts_at']).astimezone(zone)
    assert first == datetime(2026, 10, 27, 19, tzinfo=zone)


def test_flexible_calendar_indexes_excluded_dates_once_for_the_whole_horizon():
    class CountedDates(tuple):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    zone = ZoneInfo('Asia/Seoul')
    start = datetime(2026, 10, 19, tzinfo=zone)
    excluded = CountedDates((start.date(), start.date() + timedelta(days=1)))
    slots = flexible_weekly_slots(start, start + timedelta(days=14), 10,
                                  unavailable_dates=excluded)
    assert excluded.iterations == 1
    assert slots and all(slot.starts_at.astimezone(zone).date() not in excluded for slot in slots)

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from jobtology_be.contracts import StepState
from jobtology_be.modules.dashboard.next_actions import (
    DeadlineActionCandidate,
    NextActionRequest,
    RoadmapCandidate,
    RoadmapStepCandidate,
    select_next_actions,
)

REFERENCE_AT = datetime(2026, 9, 22, 9, tzinfo=UTC)


def build_request(
    *,
    deadline_actions: tuple[DeadlineActionCandidate, ...] = (),
    roadmap_steps: tuple[RoadmapStepCandidate, ...] = (),
    completed_step_ids: frozenset[str] = frozenset(),
    roadmap_is_active: bool = True,
    urgency_window: timedelta = timedelta(days=7),
    reference_at: datetime = REFERENCE_AT,
) -> NextActionRequest:
    return NextActionRequest(
        reference_at=reference_at,
        urgency_window=urgency_window,
        rule_version="next-actions-v1",
        roadmap=RoadmapCandidate(
            roadmap_id="roadmap-1",
            roadmap_version=4,
            is_active=roadmap_is_active,
        ),
        completed_step_ids=completed_step_ids,
        deadline_actions=deadline_actions,
        roadmap_steps=roadmap_steps,
    )


def build_step(
    step_id: str,
    *,
    state: StepState = StepState.TODO,
    position: int = 1,
    due_at: datetime | None = None,
    expires_at: datetime | None = None,
    source_is_valid: bool = True,
    prerequisite_step_ids: tuple[str, ...] = (),
) -> RoadmapStepCandidate:
    return RoadmapStepCandidate(
        roadmap_id="roadmap-1",
        roadmap_version=4,
        step_id=step_id,
        title=step_id,
        state=state,
        roadmap_position=position,
        due_at=due_at,
        expires_at=expires_at,
        source_is_valid=source_is_valid,
        prerequisite_step_ids=prerequisite_step_ids,
        support_refs=(f"evidence:{step_id}",),
    )


def test_select_next_actions_prioritizes_deadline_at_exact_urgency_boundary() -> None:
    # Given
    deadline = DeadlineActionCandidate(
        action_id="registration-1",
        title="Register",
        due_at=REFERENCE_AT + timedelta(days=7),
        source_is_valid=True,
        prerequisite_step_ids=(),
        support_refs=("evidence:registration-1",),
    )
    in_progress = build_step("step-progress", state=StepState.IN_PROGRESS)
    todo = build_step("step-todo", position=2)

    # When
    result = select_next_actions(
        build_request(deadline_actions=(deadline,), roadmap_steps=(todo, in_progress))
    )

    # Then
    assert [item.kind for item in result.recommendations] == [
        "REVIEW_DEADLINE",
        "CONTINUE_STEP",
        "START_STEP",
    ]
    assert result.recommendations[0].due_at == deadline.due_at


def test_select_next_actions_excludes_blocked_invalid_expired_and_completed_steps() -> None:
    # Given
    actionable = build_step("step-actionable", position=2)
    blocked = build_step("step-blocked", prerequisite_step_ids=("required-step",))
    source_invalid = build_step("step-invalid", source_is_valid=False)
    expired = build_step("step-expired", expires_at=REFERENCE_AT)
    completed = build_step("step-completed", state=StepState.COMPLETED)

    # When
    result = select_next_actions(
        build_request(
            roadmap_steps=(actionable, blocked, source_invalid, expired, completed),
            completed_step_ids=frozenset({"step-completed"}),
        )
    )

    # Then
    assert [item.step_id for item in result.recommendations] == ["step-actionable"]
    recommendation = result.recommendations[0]
    assert recommendation.roadmap_id == "roadmap-1"
    assert recommendation.roadmap_version == 4
    assert recommendation.support_refs == ("evidence:step-actionable",)


def test_select_next_actions_orders_ties_by_due_time_null_last_then_stable_id() -> None:
    # Given
    due_later = build_step("step-c", position=1, due_at=REFERENCE_AT + timedelta(days=2))
    due_earlier = build_step("step-b", position=1, due_at=REFERENCE_AT + timedelta(days=1))
    no_due = build_step("step-a", position=1)

    # When
    first = select_next_actions(build_request(roadmap_steps=(due_later, no_due, due_earlier)))
    second = select_next_actions(build_request(roadmap_steps=(no_due, due_earlier, due_later)))

    # Then
    expected_step_ids = ["step-b", "step-c", "step-a"]
    assert [item.step_id for item in first.recommendations] == expected_step_ids
    assert [item.step_id for item in second.recommendations] == expected_step_ids


def test_select_next_actions_excludes_roadmap_steps_when_roadmap_is_inactive() -> None:
    # Given
    step = build_step("step-todo")

    # When
    result = select_next_actions(build_request(roadmap_steps=(step,), roadmap_is_active=False))

    # Then
    assert result.recommendations == ()


def test_select_next_actions_continues_overdue_study_step_when_source_is_not_expired() -> None:
    # Given
    overdue = build_step(
        "step-overdue",
        state=StepState.IN_PROGRESS,
        due_at=REFERENCE_AT - timedelta(days=1),
    )

    # When
    result = select_next_actions(build_request(roadmap_steps=(overdue,)))

    # Then
    assert [item.step_id for item in result.recommendations] == ["step-overdue"]


def test_select_next_actions_orders_dst_fold_deadlines_by_elapsed_instant() -> None:
    # Given
    eastern = ZoneInfo("America/New_York")
    first_fold = build_step(
        "step-z",
        due_at=datetime(2026, 11, 1, 1, 30, tzinfo=eastern, fold=0),
    )
    second_fold = build_step(
        "step-a",
        due_at=datetime(2026, 11, 1, 1, 30, tzinfo=eastern, fold=1),
    )

    # When
    result = select_next_actions(build_request(roadmap_steps=(second_fold, first_fold)))

    # Then
    assert [item.step_id for item in result.recommendations] == ["step-z", "step-a"]


def test_select_next_actions_excludes_deadline_elapsed_before_dst_fold_reference() -> None:
    # Given
    eastern = ZoneInfo("America/New_York")
    reference_at = datetime(2026, 11, 1, 1, 15, tzinfo=eastern, fold=1)
    expired_deadline = DeadlineActionCandidate(
        action_id="deadline-expired",
        title="Expired registration",
        due_at=datetime(2026, 11, 1, 1, 30, tzinfo=eastern, fold=0),
        source_is_valid=True,
        prerequisite_step_ids=(),
        support_refs=(),
    )

    # When
    result = select_next_actions(
        build_request(
            deadline_actions=(expired_deadline,),
            reference_at=reference_at,
            urgency_window=timedelta(hours=1),
        )
    )

    # Then
    assert result.recommendations == ()


def test_select_next_actions_excludes_step_expired_before_dst_fold_reference() -> None:
    # Given
    eastern = ZoneInfo("America/New_York")
    reference_at = datetime(2026, 11, 1, 1, 15, tzinfo=eastern, fold=1)
    expired = build_step(
        "step-expired",
        expires_at=datetime(2026, 11, 1, 1, 30, tzinfo=eastern, fold=0),
    )

    # When
    result = select_next_actions(build_request(roadmap_steps=(expired,), reference_at=reference_at))

    # Then
    assert result.recommendations == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reference_at", datetime.fromisoformat("2026-09-22T09:00:00")),
        ("urgency_window", timedelta(seconds=-1)),
    ],
)
def test_next_action_request_rejects_naive_reference_or_negative_urgency_window(
    field: str, value: datetime | timedelta
) -> None:
    # Given
    request_data = build_request().model_dump()
    request_data[field] = value

    # When / Then
    with pytest.raises(ValidationError):
        _ = NextActionRequest.model_validate(request_data)

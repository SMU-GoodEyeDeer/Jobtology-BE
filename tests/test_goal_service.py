from datetime import UTC, datetime
from uuid import UUID

import pytest

from jobtology_be.application.services.goals import GoalUpdateCommand, PersistentGoalService
from jobtology_be.infrastructure.persistence.contracts import (
    GoalMutation,
    GoalSnapshot,
    ProfileSnapshot,
    UserCreate,
)


class RecordingGoalStore:
    user_creates: list[UserCreate]
    goal_mutations: list[GoalMutation]

    def __init__(self) -> None:
        self.user_creates = []
        self.goal_mutations = []

    async def create_user(self, request: UserCreate) -> ProfileSnapshot:
        self.user_creates.append(request)
        return ProfileSnapshot(user_id=request.user_id, version=1)

    async def mutate_goal(self, request: GoalMutation) -> GoalSnapshot:
        self.goal_mutations.append(request)
        return GoalSnapshot(
            goal_id=UUID("73625079-9005-4cff-9b10-95a894696690"),
            user_id=request.user_id,
            status=request.status,
        )


@pytest.mark.anyio
async def test_goal_service_maps_a_command_to_the_atomic_persistence_mutation() -> None:
    # Given
    user_id = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
    store = RecordingGoalStore()
    service = PersistentGoalService(
        store, occupation_supported=lambda occupation_id: occupation_id == "BACKEND_DEVELOPER",
    )
    command = GoalUpdateCommand(
        expected_profile_version=3,
        goal_mode="TARGETED",
        occupation_id="BACKEND_DEVELOPER",
        target_by=datetime(2027, 1, 1, tzinfo=UTC),
        timezone="Asia/Seoul",
        original_time_phrase="next year",
        status="ACTIVE",
    )

    # When
    result = await service.update_goal(user_id, command)

    # Then
    assert result.goal_id == UUID("73625079-9005-4cff-9b10-95a894696690")
    assert result.status == "ACTIVE"
    assert store.user_creates == [UserCreate(user_id=user_id)]
    assert store.goal_mutations == [
        GoalMutation(
            user_id=user_id,
            expected_profile_version=3,
            goal_id=None,
            goal_mode="TARGETED",
            occupation_id="BACKEND_DEVELOPER",
            target_by=datetime(2027, 1, 1, tzinfo=UTC),
            timezone="Asia/Seoul",
            original_time_phrase="next year",
            status="ACTIVE",
        )
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("occupation_id", "timezone", "target_delta", "code"),
    [
        ("BACKEND_DEVELOPER", "Korea/Seoul", 30, "unknown_timezone"),
        ("BACKEND_DEVELOPER", "UTC", -1, "deadline_not_future"),
        ("BACKEND_DEVELOPER", "UTC", 367, "deadline_out_of_range"),
        ("UNKNOWN_ROLE", "UTC", 30, "unsupported_occupation"),
    ],
)
async def test_goal_service_rejects_unplannable_input_before_creating_any_state(
    occupation_id: str, timezone: str, target_delta: int, code: str,
) -> None:
    from datetime import timedelta

    from jobtology_be.modules.goals.write_validation import GoalWriteValidationError

    store = RecordingGoalStore()
    service = PersistentGoalService(store, occupation_supported=lambda value: value == "BACKEND_DEVELOPER")
    with pytest.raises(GoalWriteValidationError) as error:
        await service.update_goal(UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4"), GoalUpdateCommand(
            expected_profile_version=1, goal_mode="TARGETED", occupation_id=occupation_id,
            target_by=datetime.now(UTC) + timedelta(days=target_delta), timezone=timezone,
            original_time_phrase="user deadline", status="ACTIVE",
        ))
    assert error.value.code == code
    assert store.user_creates == []
    assert store.goal_mutations == []


@pytest.mark.anyio
@pytest.mark.parametrize("unavailable_callback", [False, True])
async def test_targeted_goal_fails_closed_when_its_occupation_catalog_is_unavailable(
    unavailable_callback: bool,
) -> None:
    from datetime import timedelta

    from jobtology_be.application.services.analysis_inputs import (
        AnalysisContextInputsUnavailableError,
    )

    def unavailable(_occupation_id: str) -> bool:
        raise AnalysisContextInputsUnavailableError("occupation catalog unavailable")

    store = RecordingGoalStore()
    service = PersistentGoalService(
        store, occupation_supported=unavailable if unavailable_callback else None,
    )
    with pytest.raises(AnalysisContextInputsUnavailableError):
        await service.update_goal(UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4"), GoalUpdateCommand(
            expected_profile_version=1, goal_mode="TARGETED", occupation_id="BACKEND_DEVELOPER",
            target_by=datetime.now(UTC) + timedelta(days=30), timezone="UTC",
            original_time_phrase="in one month", status="ACTIVE",
        ))
    assert store.user_creates == []
    assert store.goal_mutations == []


@pytest.mark.anyio
async def test_archiving_an_existing_goal_accepts_its_past_deadline() -> None:
    from datetime import timedelta

    store = RecordingGoalStore()
    service = PersistentGoalService(store, occupation_supported=lambda _occupation_id: True)
    goal_id = UUID("73625079-9005-4cff-9b10-95a894696690")
    past = datetime.now(UTC) - timedelta(days=30)
    result = await service.update_goal(UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4"), GoalUpdateCommand(
        goal_id=goal_id, expected_profile_version=3, goal_mode="TARGETED",
        occupation_id="BACKEND_DEVELOPER", target_by=past, timezone="Asia/Seoul",
        original_time_phrase="last month", status="ARCHIVED",
    ))
    assert result.status == "ARCHIVED"
    assert store.goal_mutations[0].goal_id == goal_id
    assert store.goal_mutations[0].target_by == past


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["ACTIVE", "DRAFT", "ARCHIVED"])
async def test_creating_goals_still_rejects_a_past_deadline(status: str) -> None:
    from datetime import timedelta

    from jobtology_be.modules.goals.write_validation import GoalWriteValidationError

    store = RecordingGoalStore()
    service = PersistentGoalService(store, occupation_supported=lambda _occupation_id: True)
    with pytest.raises(GoalWriteValidationError) as error:
        await service.update_goal(UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4"), GoalUpdateCommand(
            expected_profile_version=1, goal_mode="TARGETED", occupation_id="BACKEND_DEVELOPER",
            target_by=datetime.now(UTC) - timedelta(days=1), timezone="UTC",
            original_time_phrase="yesterday", status=status,
        ))
    assert error.value.code == "deadline_not_future"
    assert store.goal_mutations == []

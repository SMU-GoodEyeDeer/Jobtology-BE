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
    service = PersistentGoalService(store)
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

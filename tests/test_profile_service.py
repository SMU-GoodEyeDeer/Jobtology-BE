from datetime import date
from uuid import UUID

import pytest

from jobtology_be.application.services.profiles import (
    PersistentProfileService,
    ProfileUpdateCommand,
)
from jobtology_be.infrastructure.persistence.contracts import (
    ProfileMutation,
    ProfileSnapshot,
    UserCreate,
)


class RecordingProfileStore:
    user_creates: list[UserCreate]
    profile_mutations: list[ProfileMutation]

    def __init__(self) -> None:
        self.user_creates = []
        self.profile_mutations = []

    async def create_user(self, request: UserCreate) -> ProfileSnapshot:
        self.user_creates.append(request)
        return ProfileSnapshot(user_id=request.user_id, version=1)

    async def mutate_profile(self, request: ProfileMutation) -> ProfileSnapshot:
        self.profile_mutations.append(request)
        return ProfileSnapshot(user_id=request.user_id, version=request.expected_version + 1)


@pytest.mark.anyio
async def test_profile_service_maps_validated_command_to_persistence_contract() -> None:
    # Given
    user_id = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
    store = RecordingProfileStore()
    service = PersistentProfileService(store)

    # When
    result = await service.update_profile(
        user_id,
        ProfileUpdateCommand(
            expected_profile_version=3,
            major_raw="Computer Science",
            major_concept_id="concept:computer-science",
            year=3,
            enrollment_status="ENROLLED",
            expected_graduation_on=date(2028, 2, 1),
        ),
    )

    # Then
    assert result.profile_version == 4
    assert store.user_creates == [UserCreate(user_id=user_id)]
    assert store.profile_mutations == [
        ProfileMutation(
            user_id=user_id,
            expected_version=3,
            major_raw="Computer Science",
            major_concept_id="concept:computer-science",
            year=3,
            enrollment_status="ENROLLED",
            expected_graduation_on=date(2028, 2, 1),
        )
    ]

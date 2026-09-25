from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from jobtology_be.infrastructure.persistence.contracts import (
    ProfileMutation,
    ProfileSnapshot,
    UserCreate,
)


class ProfileStore(Protocol):
    async def create_user(self, request: UserCreate) -> ProfileSnapshot: ...

    async def mutate_profile(self, request: ProfileMutation) -> ProfileSnapshot: ...


@dataclass(frozen=True, slots=True)
class ProfileUpdateCommand:
    expected_profile_version: int
    major_raw: str
    major_concept_id: str | None
    year: int | None
    enrollment_status: str | None
    expected_graduation_on: date | None


@dataclass(frozen=True, slots=True)
class ProfileUpdateResult:
    user_id: UUID
    profile_version: int


class ProfileService(Protocol):
    async def update_profile(
        self, user_id: UUID, command: ProfileUpdateCommand
    ) -> ProfileUpdateResult: ...


class PersistentProfileService:
    _store: ProfileStore

    def __init__(self, store: ProfileStore) -> None:
        self._store = store

    async def update_profile(
        self, user_id: UUID, command: ProfileUpdateCommand
    ) -> ProfileUpdateResult:
        await self._store.create_user(UserCreate(user_id=user_id))
        snapshot = await self._store.mutate_profile(
            ProfileMutation(
                user_id=user_id,
                expected_version=command.expected_profile_version,
                major_raw=command.major_raw,
                major_concept_id=command.major_concept_id,
                year=command.year,
                enrollment_status=command.enrollment_status,
                expected_graduation_on=command.expected_graduation_on,
            )
        )
        return ProfileUpdateResult(user_id=snapshot.user_id, profile_version=snapshot.version)

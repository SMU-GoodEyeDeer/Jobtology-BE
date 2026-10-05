from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from jobtology_be.infrastructure.persistence.contracts import (
    CapabilityDelete,
    CapabilityMutation,
    CapabilitySnapshot,
    JsonValue,
    OnboardingCapabilityReplace,
    OnboardingCapabilityUnit,
    ProfileSnapshot,
    UserCreate,
)


class CapabilityStore(Protocol):
    async def create_user(self, request: UserCreate) -> ProfileSnapshot: ...

    async def mutate_capability(self, request: CapabilityMutation) -> CapabilitySnapshot: ...

    async def delete_capability(self, request: CapabilityDelete) -> ProfileSnapshot: ...

    async def replace_onboarding_capabilities(
        self, request: OnboardingCapabilityReplace
    ) -> ProfileSnapshot: ...


@dataclass(frozen=True, slots=True)
class CapabilityMutationCommand:
    capability_id: UUID | None
    expected_profile_version: int
    category: str
    raw_text: str
    entity_id: str | None
    proficiency: str | None
    details: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    capability_id: UUID
    profile_version: int


@dataclass(frozen=True, slots=True)
class OnboardingCapabilitiesCommand:
    expected_profile_version: int
    occupation_id: str
    checklist_version: int
    units: tuple[OnboardingCapabilityUnit, ...]


class CapabilityService(Protocol):
    async def upsert(self, user_id: UUID, command: CapabilityMutationCommand) -> CapabilityResult: ...

    async def delete(self, user_id: UUID, capability_id: UUID, expected_profile_version: int) -> int: ...

    async def replace_onboarding(self, user_id: UUID, command: OnboardingCapabilitiesCommand) -> int: ...


class PersistentCapabilityService:
    _store: CapabilityStore

    def __init__(self, store: CapabilityStore) -> None:
        self._store = store

    async def upsert(self, user_id: UUID, command: CapabilityMutationCommand) -> CapabilityResult:
        await self._store.create_user(UserCreate(user_id=user_id))
        snapshot = await self._store.mutate_capability(
            CapabilityMutation(
                user_id=user_id,
                expected_profile_version=command.expected_profile_version,
                capability_id=command.capability_id,
                category=command.category,
                raw_text=command.raw_text,
                entity_id=command.entity_id,
                proficiency=command.proficiency,
                details=command.details,
            )
        )
        return CapabilityResult(
            capability_id=snapshot.capability_id,
            profile_version=snapshot.profile_version,
        )

    async def delete(self, user_id: UUID, capability_id: UUID, expected_profile_version: int) -> int:
        snapshot = await self._store.delete_capability(
            CapabilityDelete(
                user_id=user_id,
                capability_id=capability_id,
                expected_profile_version=expected_profile_version,
            )
        )
        return snapshot.version

    async def replace_onboarding(self, user_id: UUID, command: OnboardingCapabilitiesCommand) -> int:
        await self._store.create_user(UserCreate(user_id=user_id))
        snapshot = await self._store.replace_onboarding_capabilities(
            OnboardingCapabilityReplace(
                user_id=user_id,
                expected_profile_version=command.expected_profile_version,
                occupation_id=command.occupation_id,
                checklist_version=command.checklist_version,
                units=command.units,
            )
        )
        return snapshot.version

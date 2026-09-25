from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID, uuid4

from jobtology_be.infrastructure.persistence.contracts import (
    ProfileSnapshot,
    RoadmapCreate,
    RoadmapMutation,
    RoadmapSnapshot,
    StepStateMutation,
    UserCreate,
)

type RoadmapState = Literal["DRAFT", "ACTIVE", "ARCHIVED"]
type StepState = Literal["TODO", "IN_PROGRESS", "COMPLETED"]


class RoadmapStore(Protocol):
    async def create_user(self, request: UserCreate) -> ProfileSnapshot: ...

    async def create_roadmap(self, request: RoadmapCreate) -> RoadmapSnapshot: ...

    async def mutate_roadmap(self, request: RoadmapMutation) -> RoadmapSnapshot: ...

    async def mutate_step_state(self, request: StepStateMutation) -> RoadmapSnapshot: ...


@dataclass(frozen=True, slots=True)
class RoadmapCreateCommand:
    expected_profile_version: int
    goal_id: UUID
    proposal_id: UUID
    title: str


@dataclass(frozen=True, slots=True)
class RoadmapMutationCommand:
    roadmap_id: UUID
    expected_roadmap_version: int
    expected_profile_version: int
    state: RoadmapState
    title: str | None


@dataclass(frozen=True, slots=True)
class StepStateCommand:
    roadmap_id: UUID
    step_id: UUID
    expected_roadmap_version: int
    expected_profile_version: int
    state: StepState


@dataclass(frozen=True, slots=True)
class RoadmapResult:
    roadmap_id: UUID
    roadmap_version: int
    state: RoadmapState


class RoadmapService(Protocol):
    async def create(self, user_id: UUID, command: RoadmapCreateCommand) -> RoadmapResult: ...

    async def mutate(self, user_id: UUID, command: RoadmapMutationCommand) -> RoadmapResult: ...

    async def update_step(self, user_id: UUID, command: StepStateCommand) -> RoadmapResult: ...


class PersistentRoadmapService:
    _store: RoadmapStore

    def __init__(self, store: RoadmapStore) -> None:
        self._store = store

    async def create(self, user_id: UUID, command: RoadmapCreateCommand) -> RoadmapResult:
        await self._store.create_user(UserCreate(user_id=user_id))
        snapshot = await self._store.create_roadmap(
            RoadmapCreate(
                user_id=user_id,
                roadmap_id=uuid4(),
                goal_id=command.goal_id,
                proposal_id=command.proposal_id,
                title=command.title,
                profile_version=command.expected_profile_version,
            )
        )
        return RoadmapResult(
            roadmap_id=snapshot.roadmap_id,
            roadmap_version=snapshot.version,
            state=snapshot.state,
        )

    async def mutate(self, user_id: UUID, command: RoadmapMutationCommand) -> RoadmapResult:
        snapshot = await self._store.mutate_roadmap(
            RoadmapMutation(
                user_id=user_id,
                roadmap_id=command.roadmap_id,
                expected_roadmap_version=command.expected_roadmap_version,
                expected_profile_version=command.expected_profile_version,
                state=command.state,
                title=command.title,
            )
        )
        return RoadmapResult(
            roadmap_id=snapshot.roadmap_id,
            roadmap_version=snapshot.version,
            state=snapshot.state,
        )

    async def update_step(self, user_id: UUID, command: StepStateCommand) -> RoadmapResult:
        snapshot = await self._store.mutate_step_state(
            StepStateMutation(
                user_id=user_id,
                roadmap_id=command.roadmap_id,
                step_id=command.step_id,
                expected_roadmap_version=command.expected_roadmap_version,
                expected_profile_version=command.expected_profile_version,
                state=command.state,
            )
        )
        return RoadmapResult(
            roadmap_id=snapshot.roadmap_id,
            roadmap_version=snapshot.version,
            state=snapshot.state,
        )

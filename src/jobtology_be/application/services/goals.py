from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from jobtology_be.infrastructure.persistence.contracts import (
    GoalMutation,
    GoalSnapshot,
    ProfileSnapshot,
    UserCreate,
)

type GoalMode = Literal["TARGETED", "DISCOVERY"]
type GoalStatus = Literal["DRAFT", "ACTIVE", "ARCHIVED"]


class GoalStore(Protocol):
    async def create_user(self, request: UserCreate) -> ProfileSnapshot: ...

    async def mutate_goal(self, request: GoalMutation) -> GoalSnapshot: ...


@dataclass(frozen=True, slots=True)
class GoalUpdateCommand:
    expected_profile_version: int
    goal_mode: GoalMode
    occupation_id: str | None
    target_by: datetime
    timezone: str
    original_time_phrase: str
    status: GoalStatus
    goal_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class GoalUpdateResult:
    goal_id: UUID
    status: GoalStatus


class GoalService(Protocol):
    async def update_goal(self, user_id: UUID, command: GoalUpdateCommand) -> GoalUpdateResult: ...


class PersistentGoalService:
    _store: GoalStore

    def __init__(self, store: GoalStore) -> None:
        self._store = store

    async def update_goal(self, user_id: UUID, command: GoalUpdateCommand) -> GoalUpdateResult:
        await self._store.create_user(UserCreate(user_id=user_id))
        snapshot = await self._store.mutate_goal(
            GoalMutation(
                user_id=user_id,
                expected_profile_version=command.expected_profile_version,
                goal_id=command.goal_id,
                goal_mode=command.goal_mode,
                occupation_id=command.occupation_id,
                target_by=command.target_by,
                timezone=command.timezone,
                original_time_phrase=command.original_time_phrase,
                status=command.status,
            )
        )
        return GoalUpdateResult(goal_id=snapshot.goal_id, status=command.status)

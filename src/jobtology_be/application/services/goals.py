from collections.abc import Callable
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
from jobtology_be.modules.goals.write_validation import (
    GoalWriteValidationError,
    validate_goal_schedule,
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

    def __init__(
        self, store: GoalStore, *, occupation_supported: Callable[[str], bool] | None = None,
    ) -> None:
        self._store = store
        self._occupation_supported = occupation_supported

    async def update_goal(self, user_id: UUID, command: GoalUpdateCommand) -> GoalUpdateResult:
        validate_goal_schedule(command.target_by, command.timezone)
        if command.goal_mode == "TARGETED" and (
            command.occupation_id is None
            or not command.occupation_id.strip()
            or (
                self._occupation_supported is not None
                and not self._occupation_supported(command.occupation_id)
            )
        ):
            raise GoalWriteValidationError(
                field="occupation_id", code="unsupported_occupation",
                message="Choose a supported occupation from the occupation catalog.",
            )
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

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from jobtology_be.infrastructure.persistence.contracts import (
    ProfileSnapshot,
    RoutePreferenceMutation,
    UserCreate,
)

type BudgetMode = Literal["REGULAR", "LOW_COST"]


@dataclass(frozen=True, slots=True)
class RoutePreferences:
    profile_version: int
    available_hours_per_week: int
    availability_source: str
    budget_mode: BudgetMode
    max_out_of_pocket_krw: int | None
    fastest_path: bool
    needs_portfolio: bool
    career_switch: bool


@dataclass(frozen=True, slots=True)
class PreferencesUpdateCommand:
    expected_profile_version: int
    available_hours_per_week: int
    availability_source: str
    budget_mode: BudgetMode
    max_out_of_pocket_krw: int | None
    fastest_path: bool
    needs_portfolio: bool
    career_switch: bool


class RoutePreferencesStore(Protocol):
    async def create_user(self, request: UserCreate) -> ProfileSnapshot: ...

    async def set_route_preferences(self, request: RoutePreferenceMutation) -> ProfileSnapshot: ...


class RoutePreferencesQuery(Protocol):
    async def get_route_preferences(self, user_id: UUID) -> RoutePreferences: ...


class PreferencesService(Protocol):
    async def get_preferences(self, user_id: UUID) -> RoutePreferences: ...

    async def update_preferences(
        self, user_id: UUID, command: PreferencesUpdateCommand
    ) -> RoutePreferences: ...


class PersistentPreferencesService:
    _store: RoutePreferencesStore
    _query: RoutePreferencesQuery

    def __init__(self, store: RoutePreferencesStore, query: RoutePreferencesQuery) -> None:
        self._store = store
        self._query = query

    async def get_preferences(self, user_id: UUID) -> RoutePreferences:
        return await self._query.get_route_preferences(user_id)

    async def update_preferences(
        self, user_id: UUID, command: PreferencesUpdateCommand
    ) -> RoutePreferences:
        _ = await self._store.create_user(UserCreate(user_id=user_id))
        snapshot = await self._store.set_route_preferences(
            RoutePreferenceMutation(
                user_id=user_id,
                expected_profile_version=command.expected_profile_version,
                available_hours_per_week=command.available_hours_per_week,
                availability_source=command.availability_source,
                budget_mode=command.budget_mode,
                max_out_of_pocket_krw=command.max_out_of_pocket_krw,
                fastest_path=command.fastest_path,
                needs_portfolio=command.needs_portfolio,
                career_switch=command.career_switch,
            )
        )
        return RoutePreferences(
            profile_version=snapshot.version,
            available_hours_per_week=command.available_hours_per_week,
            availability_source=command.availability_source,
            budget_mode=command.budget_mode,
            max_out_of_pocket_krw=command.max_out_of_pocket_krw,
            fastest_path=command.fastest_path,
            needs_portfolio=command.needs_portfolio,
            career_switch=command.career_switch,
        )

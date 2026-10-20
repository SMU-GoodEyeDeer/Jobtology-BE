from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, Protocol, TypedDict
from uuid import UUID

from jobtology_be.contracts import WeeklyAvailabilityWindow
from jobtology_be.infrastructure.persistence.contracts import (
    ProfileSnapshot,
    RoutePreferenceMutation,
    UserCreate,
)

type BudgetMode = Literal["REGULAR", "LOW_COST"]

class RoutePreferenceDefaults(TypedDict):
    available_hours_per_week: int
    budget_mode: BudgetMode
    max_out_of_pocket_krw: int | None
    fastest_path: bool
    needs_portfolio: bool
    career_switch: bool
    weekly_availability: tuple[WeeklyAvailabilityWindow, ...]
    unavailable_dates: tuple[date, ...]


# Shared by read-only preference defaults and the analysis input builder. Missing
# optional onboarding preferences must not prevent the first roadmap/settings read.
DEFAULT_ROUTE_PREFERENCES: Final[RoutePreferenceDefaults] = {
    "available_hours_per_week": 10,
    "budget_mode": "REGULAR",
    "max_out_of_pocket_krw": None,
    "fastest_path": False,
    "needs_portfolio": False,
    "career_switch": False,
    "weekly_availability": (),
    "unavailable_dates": (),
}


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
    weekly_availability: tuple[WeeklyAvailabilityWindow, ...] = ()
    unavailable_dates: tuple[date, ...] = ()


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
    weekly_availability: tuple[WeeklyAvailabilityWindow, ...] = ()
    unavailable_dates: tuple[date, ...] = ()


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
                weekly_availability=command.weekly_availability,
                unavailable_dates=command.unavailable_dates,
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
            weekly_availability=command.weekly_availability,
            unavailable_dates=command.unavailable_dates,
        )

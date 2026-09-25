from dataclasses import dataclass
from uuid import UUID, uuid4

import anyio
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.infrastructure.persistence.contracts import UserCreate
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import goals, profiles, user_state_events
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

pytest_plugins = ("test_acceptance_persistence",)


@dataclass(frozen=True, slots=True)
class GoalState:
    event_count: int
    profile_version: int
    statuses: frozenset[str]


class StaticIdentityProvider:
    user_id: UUID

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


async def _create_user(database_url: str, user_id: UUID) -> None:
    database = Database.create(database_url)
    try:
        await PostgresApplicationStore(database).create_user(UserCreate(user_id=user_id))
    finally:
        await database.dispose()


async def _goal_state(database_url: str, user_id: UUID) -> GoalState:
    database = Database.create(database_url)
    try:
        async with database.sessions() as session:
            profile_version = await session.scalar(
                select(profiles.c.version).where(profiles.c.user_id == user_id)
            )
            event_count = await session.scalar(
                select(func.count()).select_from(user_state_events).where(
                    user_state_events.c.user_id == user_id
                )
            )
            statuses = frozenset(
                (await session.execute(select(goals.c.status).where(goals.c.user_id == user_id))).scalars()
            )
        assert profile_version is not None
        assert event_count is not None
        return GoalState(
            event_count=event_count,
            profile_version=profile_version,
            statuses=statuses,
        )
    finally:
        await database.dispose()


def _payload(expected_profile_version: int, status: str) -> dict[str, int | str]:
    return {
        "expected_profile_version": expected_profile_version,
        "goal_mode": "TARGETED",
        "occupation_id": "BACKEND_DEVELOPER",
        "target_by": "2027-01-01T00:00:00+00:00",
        "timezone": "UTC",
        "original_time_phrase": "by next year",
        "status": status,
    }


def test_goal_routes_return_conflict_without_rolling_back_existing_active_goal(
    acceptance_database_url: str,
) -> None:
    database_url = acceptance_database_url
    user_id = uuid4()
    anyio.run(_create_user, database_url, user_id)
    app = create_app(
        Settings(environment="test", database_url=database_url, enable_fixtures=False),
        dependencies=ApiDependencies(identity_provider=StaticIdentityProvider(user_id)),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        first_active_response = client.post("/api/v1/me/goals", json=_payload(1, "ACTIVE"))
        first_active_goal_id = first_active_response.json()["goal_id"]
        same_active_update_response = client.patch(
            f"/api/v1/me/goals/{first_active_goal_id}",
            json=_payload(2, "ACTIVE"),
        )
        second_active_response = client.post("/api/v1/me/goals", json=_payload(3, "ACTIVE"))
        draft_response = client.post("/api/v1/me/goals", json=_payload(3, "DRAFT"))
        draft_goal_id = draft_response.json()["goal_id"]
        activate_draft_response = client.patch(
            f"/api/v1/me/goals/{draft_goal_id}",
            json=_payload(4, "ACTIVE"),
        )

    assert first_active_response.status_code == 201
    assert same_active_update_response.status_code == 200
    assert second_active_response.status_code == 409
    assert second_active_response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert draft_response.status_code == 201
    assert activate_draft_response.status_code == 409
    assert activate_draft_response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert anyio.run(_goal_state, database_url, user_id) == GoalState(
        event_count=3,
        profile_version=4,
        statuses=frozenset({"ACTIVE", "DRAFT"}),
    )

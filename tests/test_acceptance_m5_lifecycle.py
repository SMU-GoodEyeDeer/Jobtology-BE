from collections.abc import Iterator
from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import anyio
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.sql import text
from test_acceptance_m5_worker import RELEASE_ID, has_completion_lineage, process_recompute

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

ACCEPTANCE_DATABASE_URL_ENV: Final = "JOBTOLOGY_ACCEPTANCE_DATABASE_URL"


@dataclass(frozen=True, slots=True)
class IsolatedDatabase:
    admin_url: str
    database_url: str
    database_name: str


@dataclass(frozen=True, slots=True)
class StaticIdentityProvider:
    user_id: UUID

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


def _acceptance_base_url() -> str:
    database_url = environ.get(ACCEPTANCE_DATABASE_URL_ENV)
    if database_url is None:
        pytest.skip(f"Set {ACCEPTANCE_DATABASE_URL_ENV} to run PostgreSQL acceptance tests")
    return database_url


async def _provision_database(base_url: str) -> IsolatedDatabase:
    url = make_url(base_url)
    database_name = f"jobtology_acceptance_{uuid4().hex}"
    admin_url = url.set(database="postgres").render_as_string(hide_password=False)
    database_url = url.set(database=database_name).render_as_string(hide_password=False)
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        await engine.dispose()
    return IsolatedDatabase(
        admin_url=admin_url,
        database_url=database_url,
        database_name=database_name,
    )


async def _drop_database(database: IsolatedDatabase) -> None:
    engine = create_async_engine(database.admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{database.database_name}" WITH (FORCE)'))
    finally:
        await engine.dispose()


def _upgrade_database(database_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


@pytest.fixture
def acceptance_database_url() -> Iterator[str]:
    database = anyio.run(_provision_database, _acceptance_base_url())
    try:
        _upgrade_database(database.database_url)
        yield database.database_url
    finally:
        anyio.run(_drop_database, database)


def _application(database_url: str, user_id: UUID) -> FastAPI:
    return create_app(
        Settings(_env_file=None, database_url=database_url, enable_fixtures=False),
        dependencies=ApiDependencies(identity_provider=StaticIdentityProvider(user_id=user_id)),
    )


def test_m5_goal_worker_and_roadmap_lifecycle_use_postgresql(
    acceptance_database_url: str,
) -> None:
    # Given
    user_id = uuid4()
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        goal_response = client.post(
            "/api/v1/me/goals",
            json={
                "expected_profile_version": 1,
                "goal_mode": "TARGETED",
                "occupation_id": "BACKEND_DEVELOPER",
                "target_by": "2026-12-31T00:00:00+00:00",
                "timezone": "UTC",
                "original_time_phrase": "by the end of 2026",
            },
        )
    assert goal_response.status_code == 201
    goal_id = UUID(goal_response.json()["goal_id"])
    publication = anyio.run(process_recompute, acceptance_database_url, user_id, 2, goal_id)

    # When
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        recompute_response = client.get(f"/api/v1/recomputations/{publication.recompute_request_id}")
        first_create = client.post(
            "/api/v1/roadmaps",
            json={
                "expected_profile_version": 2,
                "goal_id": str(goal_id),
                "proposal_id": str(publication.proposal_id),
                "title": "First roadmap",
            },
        )
        second_create = client.post(
            "/api/v1/roadmaps",
            json={
                "expected_profile_version": 2,
                "goal_id": str(goal_id),
                "proposal_id": str(publication.proposal_id),
                "title": "Replacement roadmap",
            },
        )
        first_roadmap_id = UUID(first_create.json()["roadmap_id"])
        second_roadmap_id = UUID(second_create.json()["roadmap_id"])
        first_activation = client.patch(
            f"/api/v1/roadmaps/{first_roadmap_id}",
            json={
                "operation": "ACTIVATE",
                "expected_profile_version": 2,
                "expected_roadmap_version": 1,
            },
        )
        second_activation = client.patch(
            f"/api/v1/roadmaps/{second_roadmap_id}",
            json={
                "operation": "ACTIVATE",
                "expected_profile_version": 2,
                "expected_roadmap_version": 1,
            },
        )
        stale_mutation = client.patch(
            f"/api/v1/roadmaps/{second_roadmap_id}",
            json={
                "operation": "ARCHIVE",
                "expected_profile_version": 2,
                "expected_roadmap_version": 1,
            },
        )
        first_detail = client.get(f"/api/v1/roadmaps/{first_roadmap_id}")
        second_detail = client.get(f"/api/v1/roadmaps/{second_roadmap_id}")
        step_id = UUID(second_detail.json()["steps"][0]["step_id"])
        started = client.patch(
            f"/api/v1/roadmaps/{second_roadmap_id}/steps/{step_id}",
            json={
                "state": "IN_PROGRESS",
                "expected_profile_version": 2,
                "expected_roadmap_version": 2,
            },
        )
        completed = client.patch(
            f"/api/v1/roadmaps/{second_roadmap_id}/steps/{step_id}",
            json={
                "state": "COMPLETED",
                "expected_profile_version": 3,
                "expected_roadmap_version": 3,
            },
        )
        reversed_step = client.patch(
            f"/api/v1/roadmaps/{second_roadmap_id}/steps/{step_id}",
            json={
                "state": "IN_PROGRESS",
                "expected_profile_version": 4,
                "expected_roadmap_version": 4,
            },
        )
        final_detail = client.get(f"/api/v1/roadmaps/{second_roadmap_id}")
        profile_response = client.get("/api/v1/me/profile")

    # Then
    assert recompute_response.status_code == 200
    assert recompute_response.json() == {
        "recompute_request_id": str(publication.recompute_request_id),
        "profile_version": 2,
        "state": "READY",
        "resulting_analysis_id": str(publication.analysis_id),
        "proposal_id": str(publication.proposal_id),
        "error_code": None,
    }
    assert first_create.status_code == 201
    assert second_create.status_code == 201
    assert first_activation.json() == {
        "roadmap_id": str(first_roadmap_id),
        "roadmap_version": 2,
        "state": "ACTIVE",
    }
    assert second_activation.json() == {
        "roadmap_id": str(second_roadmap_id),
        "roadmap_version": 2,
        "state": "ACTIVE",
    }
    assert stale_mutation.status_code == 409
    assert first_detail.json()["state"] == "ARCHIVED"
    assert first_detail.json()["release_id"] == RELEASE_ID
    assert second_detail.json()["state"] == "ACTIVE"
    assert second_detail.json()["release_id"] == RELEASE_ID
    assert started.json()["roadmap_version"] == 3
    assert completed.json()["roadmap_version"] == 4
    assert reversed_step.json()["roadmap_version"] == 5
    assert final_detail.json()["steps"][0]["state"] == "IN_PROGRESS"
    assert profile_response.json()["profile_version"] == 5
    assert anyio.run(has_completion_lineage, acceptance_database_url, step_id)

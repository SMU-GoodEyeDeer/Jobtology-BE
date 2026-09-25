from collections.abc import Mapping
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from test_acceptance_m5_lifecycle import _application
from test_acceptance_m5_worker import RELEASE_ID, process_recompute

from jobtology_be.infrastructure.persistence.contracts import JsonValue
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import roadmaps

pytest_plugins = ("test_acceptance_m5_lifecycle",)


async def _set_roadmap_source_validity(
    database_url: str, roadmap_id: UUID, validity: Mapping[str, JsonValue]
) -> None:
    database = Database.create(database_url)
    try:
        async with database.sessions.begin() as session:
            await session.execute(
                update(roadmaps)
                .where(roadmaps.c.id == roadmap_id)
                .values(validity=dict(validity))
            )
    finally:
        await database.dispose()


@pytest.mark.parametrize(
    "validity",
    (
        {"source_is_valid": False},
        {"expires_at": "2000-01-01T00:00:00+00:00"},
    ),
)
def test_roadmap_activation_rejects_a_noncurrent_persisted_source(
    acceptance_database_url: str, validity: Mapping[str, JsonValue]
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

    with TestClient(_application(acceptance_database_url, user_id)) as client:
        roadmap_response = client.post(
            "/api/v1/roadmaps",
            json={
                "expected_profile_version": 2,
                "goal_id": str(goal_id),
                "proposal_id": str(publication.proposal_id),
                "title": "Server-owned roadmap source",
            },
        )
    assert roadmap_response.status_code == 201
    roadmap_id = UUID(roadmap_response.json()["roadmap_id"])
    anyio.run(_set_roadmap_source_validity, acceptance_database_url, roadmap_id, validity)

    # When
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        activation_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}",
            json={
                "operation": "ACTIVATE",
                "expected_profile_version": 2,
                "expected_roadmap_version": 1,
            },
        )
        roadmap_detail = client.get(f"/api/v1/roadmaps/{roadmap_id}")

    # Then
    assert activation_response.status_code == 409
    assert activation_response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert roadmap_detail.status_code == 200
    assert roadmap_detail.json()["release_id"] == RELEASE_ID
    assert roadmap_detail.json()["state"] == "DRAFT"
    assert roadmap_detail.json()["roadmap_version"] == 1

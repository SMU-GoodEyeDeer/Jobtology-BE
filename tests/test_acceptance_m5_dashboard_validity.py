from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from test_acceptance_m5_automatic_lifecycle import _run_worker
from test_acceptance_m5_automatic_loop import (
    _application,
    _configure_preferences,
    _write_snapshot,
)

from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import roadmaps

pytest_plugins = ("test_acceptance_m5_lifecycle",)


async def _mark_roadmap_source_invalid(database_url: str, roadmap_id: UUID) -> None:
    database = Database.create(database_url)
    try:
        async with database.sessions.begin() as session:
            _ = await session.execute(
                update(roadmaps)
                .where(roadmaps.c.id == roadmap_id)
                .values(validity={"source_is_valid": False})
            )
    finally:
        await database.dispose()


def test_dashboard_excludes_actions_after_trusted_source_invalidation(
    acceptance_database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    snapshot_path = tmp_path / "published-snapshots.json"
    _write_snapshot(snapshot_path)
    monkeypatch.setenv("JOBTOLOGY_CORPUS_SNAPSHOT_PATH", str(snapshot_path))
    user_id = uuid4()
    target_by = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        profile_response = client.put(
            "/api/v1/me/profile",
            json={
                "expected_profile_version": 1,
                "major_raw": "Computer Science",
                "major_concept_id": "computer-science",
                "year": 3,
                "enrollment_status": "ENROLLED",
                "expected_graduation_on": "2027-02-01",
            },
        )
        goal_response = client.post(
            "/api/v1/me/goals",
            json={
                "expected_profile_version": 2,
                "goal_mode": "TARGETED",
                "occupation_id": "BACKEND_DEVELOPER",
                "target_by": target_by,
                "timezone": "UTC",
                "original_time_phrase": "within a month",
            },
        )
        assert profile_response.status_code == 200
        assert goal_response.status_code == 201
        goal_id = UUID(goal_response.json()["goal_id"])
        _configure_preferences(client, 3)
        capability_response = client.post(
            "/api/v1/me/capabilities",
            json={
                "expected_profile_version": 4,
                "category": "SELF_REPORTED",
                "raw_text": "API implementation",
                "entity_id": "capability-api",
                "details": {"experience_codes": []},
            },
        )
        assert capability_response.status_code == 201
        analysis_response = client.post(
            "/api/v1/analyses",
            json={
                "goal_id": str(goal_id),
                "expected_profile_version": 5,
                "basis_type": "EDITORIAL",
            },
        )

    assert analysis_response.status_code == 202
    recompute_request_id = UUID(analysis_response.json()["recompute_request_id"])
    _run_worker(acceptance_database_url, snapshot_path)

    # When
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        recompute_response = client.get(f"/api/v1/recomputations/{recompute_request_id}")
        proposal_id = UUID(recompute_response.json()["proposal_id"])
        malicious_create_response = client.post(
            "/api/v1/roadmaps",
            json={
                "expected_profile_version": 5,
                "goal_id": str(goal_id),
                "proposal_id": str(proposal_id),
                "title": "Maliciously invalid roadmap",
                "release_id": "attacker-controlled-release",
                "validity": {"source_is_valid": False},
            },
        )
        assert malicious_create_response.status_code == 422
        roadmap_response = client.post(
            "/api/v1/roadmaps",
            json={
                "expected_profile_version": 5,
                "goal_id": str(goal_id),
                "proposal_id": str(proposal_id),
                "title": "Revoked source roadmap",
            },
        )
        assert roadmap_response.status_code == 201, roadmap_response.text
        roadmap_id = UUID(roadmap_response.json()["roadmap_id"])
        authoritative_detail = client.get(f"/api/v1/roadmaps/{roadmap_id}")
        activation_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}",
            json={
                "operation": "ACTIVATE",
                "expected_profile_version": 5,
                "expected_roadmap_version": 1,
            },
        )

    anyio.run(_mark_roadmap_source_invalid, acceptance_database_url, roadmap_id)

    with TestClient(_application(acceptance_database_url, user_id)) as client:
        roadmap_detail = client.get(f"/api/v1/roadmaps/{roadmap_id}")
        dashboard_response = client.get(f"/api/v1/dashboard?goal_id={goal_id}")

    # Then
    assert recompute_response.status_code == 200
    assert recompute_response.json()["state"] == "READY"
    assert authoritative_detail.status_code == 200
    assert authoritative_detail.json()["release_id"] == "release-reviewed-v1"
    assert activation_response.status_code == 200
    assert roadmap_detail.status_code == 200
    assert roadmap_detail.json()["steps"]
    assert roadmap_detail.json()["validity"] == {"source_is_valid": False}
    assert dashboard_response.status_code == 200
    assert dashboard_response.json()["active_roadmap_id"] == str(roadmap_id)
    assert dashboard_response.json()["next_actions"] == []

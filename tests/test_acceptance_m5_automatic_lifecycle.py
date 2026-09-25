import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_acceptance_m5_automatic_loop import (
    _application,
    _configure_preferences,
    _write_snapshot,
)

from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import recompute_requests

pytest_plugins = ("test_acceptance_m5_lifecycle",)


def _run_worker(database_url: str, snapshot_path: Path) -> None:
    process = subprocess.run(
        [sys.executable, "-m", "jobtology_be.workers.main"],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "JOBTOLOGY_DATABASE_URL": database_url,
            "JOBTOLOGY_CORPUS_SNAPSHOT_PATH": str(snapshot_path),
        },
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr


async def _pending_recompute_id(
    database_url: str, user_id: UUID, profile_version: int
) -> UUID:
    database = Database.create(database_url)
    try:
        async with database.sessions() as session:
            return (
                await session.execute(
                    select(recompute_requests.c.id).where(
                        recompute_requests.c.user_id == user_id,
                        recompute_requests.c.profile_version == profile_version,
                        recompute_requests.c.state == "PENDING",
                    )
                )
            ).scalar_one()
    finally:
        await database.dispose()


def test_configured_api_worker_carries_completion_and_reversal_context(
    acceptance_database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    snapshot_path = tmp_path / "published-snapshots.json"
    _write_snapshot(snapshot_path)
    monkeypatch.setenv("JOBTOLOGY_CORPUS_SNAPSHOT_PATH", str(snapshot_path))
    user_id = uuid4()
    target_by = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    with TestClient(
        _application(acceptance_database_url, user_id), raise_server_exceptions=False
    ) as client:
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
        assert profile_response.status_code == 200
        assert profile_response.json()["profile_version"] == 2
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
        assert capability_response.json()["profile_version"] == 5

        # When
        initial_response = client.post(
            "/api/v1/analyses",
            headers={"Idempotency-Key": "automatic-lifecycle-1"},
            json={
                "goal_id": str(goal_id),
                "expected_profile_version": 5,
                "basis_type": "EDITORIAL",
            },
        )
        replay_response = client.post(
            "/api/v1/analyses",
            headers={"Idempotency-Key": "automatic-lifecycle-1"},
            json={
                "goal_id": str(goal_id),
                "expected_profile_version": 5,
                "basis_type": "EDITORIAL",
            },
        )

    # Then
    assert initial_response.status_code == 202
    assert initial_response.json()["state"] == "PENDING"
    assert replay_response.status_code == 202
    assert replay_response.json() == initial_response.json()
    initial_recompute_id = UUID(initial_response.json()["recompute_request_id"])

    _run_worker(acceptance_database_url, snapshot_path)

    with TestClient(_application(acceptance_database_url, user_id)) as client:
        initial_recompute = client.get(f"/api/v1/recomputations/{initial_recompute_id}")
    assert initial_recompute.status_code == 200
    assert initial_recompute.json()["state"] == "READY"
    assert initial_recompute.json()["profile_version"] == 5
    initial_proposal_id = UUID(initial_recompute.json()["proposal_id"])

    with TestClient(_application(acceptance_database_url, user_id)) as client:
        roadmap_response = client.post(
            "/api/v1/roadmaps",
            json={
                "expected_profile_version": 5,
                    "goal_id": str(goal_id),
                    "proposal_id": str(initial_proposal_id),
                    "title": "Automatic lifecycle roadmap",
                },
            )
        assert roadmap_response.status_code == 201
        roadmap_id = UUID(roadmap_response.json()["roadmap_id"])
        activation_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}",
            json={
                "operation": "ACTIVATE",
                "expected_profile_version": 5,
                "expected_roadmap_version": 1,
            },
        )
        assert activation_response.status_code == 200
        roadmap_detail = client.get(f"/api/v1/roadmaps/{roadmap_id}")
        assert roadmap_detail.status_code == 200
        assert roadmap_detail.json()["release_id"] == "release-reviewed-v1"
        step_id = UUID(roadmap_detail.json()["steps"][0]["step_id"])
        started_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}/steps/{step_id}",
            json={
                "state": "IN_PROGRESS",
                "expected_profile_version": 5,
                "expected_roadmap_version": 2,
            },
        )
        assert started_response.status_code == 200
        completed_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}/steps/{step_id}",
            json={
                "state": "COMPLETED",
                "expected_profile_version": 6,
                "expected_roadmap_version": 3,
            },
        )

    assert completed_response.status_code == 200
    completion_recompute_id = anyio.run(
        _pending_recompute_id, acceptance_database_url, user_id, 7
    )
    _run_worker(acceptance_database_url, snapshot_path)

    with TestClient(_application(acceptance_database_url, user_id)) as client:
        completion_recompute = client.get(f"/api/v1/recomputations/{completion_recompute_id}")
        assert completion_recompute.status_code == 200
        assert completion_recompute.json()["state"] == "READY"
        completion_analysis_id = UUID(completion_recompute.json()["resulting_analysis_id"])
        completion_analysis = client.get(f"/api/v1/analyses/{completion_analysis_id}")
        reversal_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}/steps/{step_id}",
            json={
                "state": "IN_PROGRESS",
                "expected_profile_version": 7,
                "expected_roadmap_version": 4,
            },
        )

    assert completion_analysis.status_code == 200
    assert completion_analysis.json()["results"]["requirements"][0]["status"] == "SATISFIED"
    assert reversal_response.status_code == 200
    reversal_recompute_id = anyio.run(
        _pending_recompute_id, acceptance_database_url, user_id, 8
    )
    _run_worker(acceptance_database_url, snapshot_path)

    with TestClient(_application(acceptance_database_url, user_id)) as client:
        reversal_recompute = client.get(f"/api/v1/recomputations/{reversal_recompute_id}")

    assert reversal_recompute.status_code == 200
    assert reversal_recompute.json()["state"] == "READY"
    assert reversal_recompute.json()["profile_version"] == 8

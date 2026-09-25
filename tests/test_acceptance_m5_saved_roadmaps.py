from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_acceptance_m5_automatic_lifecycle import _run_worker
from test_acceptance_m5_automatic_loop import (
    _application,
    _configure_preferences,
    _write_snapshot,
)

pytest_plugins = ("test_acceptance_m5_lifecycle",)


def test_saved_roadmap_detail_rename_diff_activation_and_archive_use_postgresql(
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
    with TestClient(
        _application(acceptance_database_url, user_id), raise_server_exceptions=False
    ) as client:
        recompute_response = client.get(f"/api/v1/recomputations/{recompute_request_id}")
        proposal_id = UUID(recompute_response.json()["proposal_id"])
        create_response = client.post(
            "/api/v1/roadmaps",
            json={
                "expected_profile_version": 5,
                "goal_id": str(goal_id),
                "proposal_id": str(proposal_id),
                "title": "Draft roadmap",
            },
        )
        assert create_response.status_code == 201, create_response.text
        roadmap_id = UUID(create_response.json()["roadmap_id"])
        list_response = client.get("/api/v1/roadmaps")
        draft_detail = client.get(f"/api/v1/roadmaps/{roadmap_id}")
        rename_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}",
            json={
                "operation": "RENAME",
                "expected_profile_version": 5,
                "expected_roadmap_version": 1,
                "title": "Renamed roadmap",
            },
        )
        renamed_detail = client.get(f"/api/v1/roadmaps/{roadmap_id}")
        diff_response = client.get(
            f"/api/v1/roadmaps/{roadmap_id}/diff?proposal_id={proposal_id}"
        )
        activation_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}",
            json={
                "operation": "ACTIVATE",
                "expected_profile_version": 5,
                "expected_roadmap_version": 2,
            },
        )
        archive_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}",
            json={
                "operation": "ARCHIVE",
                "expected_profile_version": 5,
                "expected_roadmap_version": 3,
            },
        )
        archived_detail = client.get(f"/api/v1/roadmaps/{roadmap_id}")

    with TestClient(
        _application(acceptance_database_url, uuid4()), raise_server_exceptions=False
    ) as foreign_client:
        foreign_detail = foreign_client.get(f"/api/v1/roadmaps/{roadmap_id}")
        foreign_diff = foreign_client.get(
            f"/api/v1/roadmaps/{roadmap_id}/diff?proposal_id={proposal_id}"
        )

    # Then
    assert recompute_response.status_code == 200
    assert recompute_response.json()["state"] == "READY"
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["roadmap_id"] == str(roadmap_id)
    assert draft_detail.status_code == 200
    assert draft_detail.json()["state"] == "DRAFT"
    assert rename_response.json() == {
        "roadmap_id": str(roadmap_id),
        "roadmap_version": 2,
        "state": "DRAFT",
    }
    assert renamed_detail.json()["title"] == "Renamed roadmap"
    assert diff_response.status_code == 200
    assert diff_response.json()["roadmap_id"] == str(roadmap_id)
    assert diff_response.json()["proposal_id"] == str(proposal_id)
    assert activation_response.json()["state"] == "ACTIVE"
    assert archive_response.json()["state"] == "ARCHIVED"
    assert archived_detail.json()["state"] == "ARCHIVED"
    assert foreign_detail.status_code == 404
    assert foreign_diff.status_code == 404

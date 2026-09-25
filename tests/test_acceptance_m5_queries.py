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


def test_configured_query_routes_return_published_user_scoped_results(
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
        occupations_response = client.get("/api/v1/occupations")
        assert occupations_response.status_code == 200
        assert occupations_response.json() == [
            {
                "occupation_id": "BACKEND_DEVELOPER",
                "basis_version": "reviewed-v1",
                "release_id": "release-reviewed-v1",
            }
        ]
        proposal_response = client.get(f"/api/v1/route-proposals/{proposal_id}")
        assert proposal_response.status_code == 200
        assert proposal_response.json()["steps"]
        trace_id = UUID(proposal_response.json()["decision_trace_id"])
        trace_response = client.get(f"/api/v1/traces/{trace_id}")
        dashboard_response = client.get(f"/api/v1/dashboard?goal_id={goal_id}")

    other_user_id = uuid4()
    with TestClient(_application(acceptance_database_url, other_user_id)) as client:
        other_proposal_response = client.get(f"/api/v1/route-proposals/{proposal_id}")
        other_trace_response = client.get(f"/api/v1/traces/{trace_id}")
        other_dashboard_response = client.get(f"/api/v1/dashboard?goal_id={goal_id}")

    # Then
    assert recompute_response.status_code == 200
    assert recompute_response.json()["state"] == "READY"
    assert trace_response.status_code == 200
    assert trace_response.json()["outputs"]
    assert dashboard_response.status_code == 200
    assert dashboard_response.json()["goal_id"] == str(goal_id)
    assert dashboard_response.json()["analysis_id"] == recompute_response.json()["resulting_analysis_id"]
    assert other_proposal_response.status_code == 404
    assert other_trace_response.status_code == 404
    assert other_dashboard_response.status_code == 404


def test_dashboard_hides_a_latest_analysis_when_the_profile_has_advanced(
    acceptance_database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    snapshot_path = tmp_path / "published-snapshots.json"
    _write_snapshot(snapshot_path)
    monkeypatch.setenv("JOBTOLOGY_CORPUS_SNAPSHOT_PATH", str(snapshot_path))
    user_id = uuid4()
    target_by = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        assert client.put(
            "/api/v1/me/profile",
            json={
                "expected_profile_version": 1,
                "major_raw": "Computer Science",
                "major_concept_id": "computer-science",
                "year": 3,
                "enrollment_status": "ENROLLED",
                "expected_graduation_on": "2027-02-01",
            },
        ).status_code == 200
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
        assert client.post(
            "/api/v1/me/capabilities",
            json={
                "expected_profile_version": 4,
                "category": "SELF_REPORTED",
                "raw_text": "API implementation",
                "entity_id": "capability-api",
                "details": {"experience_codes": []},
            },
        ).status_code == 201
        analysis_response = client.post(
            "/api/v1/analyses",
            json={
                "goal_id": str(goal_id),
                "expected_profile_version": 5,
                "basis_type": "EDITORIAL",
            },
        )

    assert analysis_response.status_code == 202
    initial_recompute_id = UUID(analysis_response.json()["recompute_request_id"])
    _run_worker(acceptance_database_url, snapshot_path)

    with TestClient(_application(acceptance_database_url, user_id)) as client:
        initial_recompute = client.get(f"/api/v1/recomputations/{initial_recompute_id}")
        initial_dashboard = client.get(f"/api/v1/dashboard?goal_id={goal_id}")
        profile_advance = client.put(
            "/api/v1/me/profile",
            json={
                "expected_profile_version": 5,
                "major_raw": "Software Engineering",
                "major_concept_id": "software-engineering",
                "year": 3,
                "enrollment_status": "ENROLLED",
                "expected_graduation_on": "2027-02-01",
            },
        )
        stale_dashboard = client.get(f"/api/v1/dashboard?goal_id={goal_id}")

    # When / Then
    assert initial_recompute.status_code == 200
    assert initial_recompute.json()["state"] == "READY"
    assert initial_dashboard.status_code == 200
    assert initial_dashboard.json()["analysis_id"] == initial_recompute.json()["resulting_analysis_id"]
    assert profile_advance.status_code == 200
    assert profile_advance.json()["profile_version"] == 6
    assert stale_dashboard.status_code == 200
    assert stale_dashboard.json()["state"] == "RECOMPUTING"
    assert stale_dashboard.json()["analysis_id"] is None
    assert stale_dashboard.json()["recompute_state"] == "PENDING"


def test_dashboard_does_not_show_a_different_goals_pending_recompute(
    acceptance_database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    snapshot_path = tmp_path / "published-snapshots.json"
    _write_snapshot(snapshot_path)
    monkeypatch.setenv("JOBTOLOGY_CORPUS_SNAPSHOT_PATH", str(snapshot_path))
    user_id = uuid4()
    target_by = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        first_goal_response = client.post(
            "/api/v1/me/goals",
            json={
                "expected_profile_version": 1,
                "goal_mode": "TARGETED",
                "occupation_id": "BACKEND_DEVELOPER",
                "target_by": target_by,
                "timezone": "UTC",
                "original_time_phrase": "within a month",
                "status": "DRAFT",
            },
        )
        assert first_goal_response.status_code == 201
        first_goal_id = UUID(first_goal_response.json()["goal_id"])
        _configure_preferences(client, 2)
        second_goal_response = client.post(
            "/api/v1/me/goals",
            json={
                "expected_profile_version": 3,
                "goal_mode": "TARGETED",
                "occupation_id": "BACKEND_DEVELOPER",
                "target_by": target_by,
                "timezone": "UTC",
                "original_time_phrase": "within a month",
            },
        )
        assert second_goal_response.status_code == 201
        second_goal_id = UUID(second_goal_response.json()["goal_id"])
        second_analysis_response = client.post(
            "/api/v1/analyses",
            json={
                "goal_id": str(second_goal_id),
                "expected_profile_version": 4,
                "basis_type": "EDITORIAL",
            },
        )
        assert second_analysis_response.status_code == 202

        # When
        first_dashboard = client.get(f"/api/v1/dashboard?goal_id={first_goal_id}")
        second_dashboard = client.get(f"/api/v1/dashboard?goal_id={second_goal_id}")

    # Then
    assert first_dashboard.status_code == 200
    assert first_dashboard.json()["goal_id"] == str(first_goal_id)
    assert first_dashboard.json()["state"] == "EMPTY"
    assert first_dashboard.json()["recompute_request_id"] is None
    assert second_dashboard.status_code == 200
    assert second_dashboard.json()["goal_id"] == str(second_goal_id)
    assert second_dashboard.json()["state"] == "RECOMPUTING"
    assert second_dashboard.json()["recompute_request_id"] is not None

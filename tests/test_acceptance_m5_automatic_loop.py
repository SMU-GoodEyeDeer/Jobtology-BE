import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

pytest_plugins = ("test_acceptance_m5_lifecycle",)


@dataclass(frozen=True, slots=True)
class StaticIdentityProvider:
    user_id: UUID

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


def _application(database_url: str, user_id: UUID) -> FastAPI:
    return create_app(
        Settings(database_url=database_url, enable_fixtures=False),
        dependencies=ApiDependencies(identity_provider=StaticIdentityProvider(user_id=user_id)),
    )


def _write_snapshot(path: Path) -> None:
    _ = path.write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "occupation_id": "BACKEND_DEVELOPER",
                        "basis_version": "reviewed-v1",
                        "release": {
                            "release_id": "release-reviewed-v1",
                            "state": "PUBLISHED",
                            "reviewed_at": "2026-09-22T00:00:00+00:00",
                        },
                        "is_fixture": False,
                        "capability_entries": [
                            {
                                "entity_id": "capability-api",
                                "aliases": ["API implementation"],
                            }
                        ],
                        "allowed_experience_codes": ["DELIVERED"],
                        "requirements": [
                            {
                                "requirement_key": "api",
                                "label": "API implementation",
                                "necessity": "REQUIRED",
                                "entity_id": "capability-api",
                                "required_experience_codes": ["DELIVERED"],
                                "support_refs": ["review:api"],
                            }
                        ],
                        "templates": [
                            {
                                "action_id": "api-project",
                                "revision": 1,
                                "title": "API project",
                                "estimated_hours": 1,
                                "outcome_requirement_keys": ["api"],
                                "completion_criteria": ["Publish an API"],
                                "support_refs": ["template:api-project"],
                                "cost": {"kind": "KNOWN", "krw": 0},
                                "is_foundational": False,
                            }
                        ],
                    }
                ]
            }
        )
    )


def _create_goal(client: TestClient) -> UUID:
    response = client.post(
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
    assert response.status_code == 201
    return UUID(response.json()["goal_id"])


def _configure_preferences(client: TestClient, expected_version: int) -> None:
    response = client.put(
        "/api/v1/me/route-preferences",
        json={
            "expected_profile_version": expected_version,
            "available_hours_per_week": 4,
            "availability_source": "FLEXIBLE_WEEKLY",
            "budget_mode": "REGULAR",
            "max_out_of_pocket_krw": None,
            "fastest_path": False,
            "needs_portfolio": False,
            "career_switch": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["profile_version"] == expected_version + 1


def test_analysis_request_replays_a_completed_idempotency_key(
    acceptance_database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    snapshot_path = tmp_path / "published-snapshots.json"
    _write_snapshot(snapshot_path)
    monkeypatch.setenv("JOBTOLOGY_CORPUS_SNAPSHOT_PATH", str(snapshot_path))
    user_id = uuid4()
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        goal_id = _create_goal(client)
        _configure_preferences(client, 2)

        # When
        first_response = client.post(
            "/api/v1/analyses",
            headers={"Idempotency-Key": "analysis-replay-1"},
            json={
                "goal_id": str(goal_id),
                "expected_profile_version": 3,
                "basis_type": "EDITORIAL",
            },
        )
        replay_response = client.post(
            "/api/v1/analyses",
            headers={"Idempotency-Key": "analysis-replay-1"},
            json={
                "goal_id": str(goal_id),
                "expected_profile_version": 3,
                "basis_type": "EDITORIAL",
            },
        )

    # Then
    assert first_response.status_code == 202
    assert first_response.json()["state"] == "PENDING"
    assert replay_response.status_code == 202
    assert replay_response.json() == first_response.json()


def test_analysis_request_rejects_a_reused_key_for_a_different_payload(
    acceptance_database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    snapshot_path = tmp_path / "published-snapshots.json"
    _write_snapshot(snapshot_path)
    monkeypatch.setenv("JOBTOLOGY_CORPUS_SNAPSHOT_PATH", str(snapshot_path))
    user_id = uuid4()
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        goal_id = _create_goal(client)
        _configure_preferences(client, 2)
        first_response = client.post(
            "/api/v1/analyses",
            headers={"Idempotency-Key": "analysis-conflict-1"},
            json={
                "goal_id": str(goal_id),
                "expected_profile_version": 3,
                "basis_type": "EDITORIAL",
            },
        )

        # When
        conflict_response = client.post(
            "/api/v1/analyses",
            headers={"Idempotency-Key": "analysis-conflict-1"},
            json={
                "goal_id": str(goal_id),
                "expected_profile_version": 1,
                "basis_type": "EDITORIAL",
            },
        )

    # Then
    assert first_response.status_code == 202
    assert conflict_response.status_code == 409
    assert conflict_response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_analysis_request_rejects_a_stale_profile_version(
    acceptance_database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    snapshot_path = tmp_path / "published-snapshots.json"
    _write_snapshot(snapshot_path)
    monkeypatch.setenv("JOBTOLOGY_CORPUS_SNAPSHOT_PATH", str(snapshot_path))
    user_id = uuid4()
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        goal_id = _create_goal(client)

        # When
        response = client.post(
            "/api/v1/analyses",
            json={
                "goal_id": str(goal_id),
                "expected_profile_version": 1,
                "basis_type": "EDITORIAL",
            },
        )

    # Then
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"

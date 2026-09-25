import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import anyio
from fastapi.testclient import TestClient
from test_acceptance_m5_lifecycle import _application
from test_acceptance_m5_worker import (
    CAPABILITY_ENTITY_ID,
    CAPABILITY_LABEL,
    EXPERIENCE_CODE,
    RELEASE_ID,
    REQUIREMENT_KEY,
    enqueue_recompute,
)

pytest_plugins = ("test_acceptance_m5_lifecycle",)


def _write_snapshot(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "occupation_id": "BACKEND_DEVELOPER",
                        "basis_version": "reviewed-v1",
                        "release": {
                            "release_id": RELEASE_ID,
                            "state": "PUBLISHED",
                            "reviewed_at": "2026-09-22T00:00:00+00:00",
                        },
                        "is_fixture": False,
                        "capability_entries": [
                            {
                                "entity_id": CAPABILITY_ENTITY_ID,
                                "aliases": [CAPABILITY_LABEL],
                            }
                        ],
                        "allowed_experience_codes": [EXPERIENCE_CODE],
                        "requirements": [
                            {
                                "requirement_key": REQUIREMENT_KEY,
                                "label": CAPABILITY_LABEL,
                                "necessity": "REQUIRED",
                                "entity_id": CAPABILITY_ENTITY_ID,
                                "required_experience_codes": [EXPERIENCE_CODE],
                                "support_refs": ["review:api"],
                            }
                        ],
                        "templates": [
                            {
                                "action_id": "api-project",
                                "revision": 1,
                                "title": "API project",
                                "estimated_hours": 1,
                                "outcome_requirement_keys": [REQUIREMENT_KEY],
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


def test_configured_worker_entrypoint_publishes_a_context_bound_request(
    acceptance_database_url: str, tmp_path: Path
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
    request_id = anyio.run(enqueue_recompute, acceptance_database_url, user_id, 2, goal_id)
    snapshot_path = tmp_path / "published-snapshots.json"
    _write_snapshot(snapshot_path)

    # When
    process = subprocess.run(
        [sys.executable, "-m", "jobtology_be.workers.main"],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "JOBTOLOGY_DATABASE_URL": acceptance_database_url,
            "JOBTOLOGY_CORPUS_SNAPSHOT_PATH": str(snapshot_path),
        },
        text=True,
        timeout=30,
    )

    # Then
    assert process.returncode == 0, process.stderr
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        recompute_response = client.get(f"/api/v1/recomputations/{request_id}")
    assert recompute_response.status_code == 200
    assert recompute_response.json()["state"] == "READY"

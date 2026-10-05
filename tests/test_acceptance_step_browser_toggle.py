from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import anyio
from fastapi.testclient import TestClient
from test_acceptance_m5_capability_outcomes import DerivedCapability, _derived_capability
from test_acceptance_m5_lifecycle import _application
from test_acceptance_m5_worker import (
    CAPABILITY_ENTITY_ID,
    CAPABILITY_LABEL,
    EXPERIENCE_CODE,
    process_recompute,
)

pytest_plugins = ("test_acceptance_m5_lifecycle",)


def test_browser_toggle_completes_and_reverts_a_step_without_an_in_progress_hop(
    acceptance_database_url: str,
) -> None:
    # Given an active roadmap whose first step is TODO
    user_id = uuid4()
    target_by = datetime.now(UTC) + timedelta(days=30)
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        goal_response = client.post(
            "/api/v1/me/goals",
            json={
                "expected_profile_version": 1,
                "goal_mode": "TARGETED",
                "occupation_id": "BACKEND_DEVELOPER",
                "target_by": target_by.isoformat(),
                "timezone": "UTC",
                "original_time_phrase": "by the end of 2026",
            },
        )
    goal_id = UUID(goal_response.json()["goal_id"])
    publication = anyio.run(
        process_recompute, acceptance_database_url, user_id, 2, goal_id, (), target_by
    )
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        roadmap_id = UUID(
            client.post(
                "/api/v1/roadmaps",
                json={
                    "expected_profile_version": 2,
                    "goal_id": str(goal_id),
                    "proposal_id": str(publication.proposal_id),
                    "title": "Browser toggle roadmap",
                },
            ).json()["roadmap_id"]
        )
        _ = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}",
            json={"operation": "ACTIVATE", "expected_profile_version": 2, "expected_roadmap_version": 1},
        )
        step_id = UUID(client.get(f"/api/v1/roadmaps/{roadmap_id}").json()["steps"][0]["step_id"])

        # When the deployed browser marks the TODO step complete, then cancels it
        completed = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}/steps/{step_id}",
            json={"state": "COMPLETED", "expected_profile_version": 2, "expected_roadmap_version": 2},
        )
        completed_capability = anyio.run(_derived_capability, acceptance_database_url, step_id)
        reverted = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}/steps/{step_id}",
            json={"state": "TODO", "expected_profile_version": 3, "expected_roadmap_version": 3},
        )
        detail = client.get(f"/api/v1/roadmaps/{roadmap_id}").json()

    # Then both direct transitions succeed and the earned capability is revoked on cancel
    assert completed.status_code == 200
    assert completed_capability == DerivedCapability(
        raw_text=CAPABILITY_LABEL,
        entity_id=CAPABILITY_ENTITY_ID,
        experience_codes=(EXPERIENCE_CODE,),
        lifecycle="ACTIVE",
    )
    assert reverted.status_code == 200
    assert detail["steps"][0]["state"] == "TODO"
    assert detail["version"] == 4
    revoked = anyio.run(_derived_capability, acceptance_database_url, step_id)
    assert revoked is not None and revoked.lifecycle == "REVOKED"

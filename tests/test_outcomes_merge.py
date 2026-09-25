from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import anyio
from fastapi.testclient import TestClient

from jobtology_be.contracts import RoadmapOutcome
from jobtology_be.infrastructure.persistence.completion_outcomes import (
    CanonicalCompletionOutcome,
    RequirementOutcomeProvenance,
    canonical_completion_outcomes,
)
from test_support.outcomes_merge_data import (
    CAPABILITY_ENTITY_ID,
    DELIVERY_CODE,
    DELIVERY_LABEL,
    DELIVERY_REQUIREMENT_KEY,
    FOUNDATION_CODE,
    FOUNDATION_LABEL,
    FOUNDATION_REQUIREMENT_KEY,
    RELEASE_ID,
    DerivedCompletionCapability,
    StaticSnapshotReader,
    shared_entity_snapshot,
)
from test_support.outcomes_merge_runtime import (
    application,
    completion_capabilities,
    enqueue_recompute,
    process_one_recompute,
)

pytest_plugins = ("test_acceptance_m5_lifecycle",)


def test_canonical_completion_outcomes_merge_same_entity_deterministically() -> None:
    # Given
    outcomes = (
        RoadmapOutcome(
            requirement_key=FOUNDATION_REQUIREMENT_KEY,
            entity_id=CAPABILITY_ENTITY_ID,
            raw_text=FOUNDATION_LABEL,
            experience_codes=(FOUNDATION_CODE,),
        ),
        RoadmapOutcome(
            requirement_key=DELIVERY_REQUIREMENT_KEY,
            entity_id=CAPABILITY_ENTITY_ID,
            raw_text=DELIVERY_LABEL,
            experience_codes=(DELIVERY_CODE,),
        ),
    )

    # When
    canonical_outcomes = canonical_completion_outcomes(outcomes)

    # Then
    assert canonical_outcomes == (
        CanonicalCompletionOutcome(
            entity_id=CAPABILITY_ENTITY_ID,
            raw_text=DELIVERY_LABEL,
            requirement_keys=(DELIVERY_REQUIREMENT_KEY, FOUNDATION_REQUIREMENT_KEY),
            experience_codes=(DELIVERY_CODE, FOUNDATION_CODE),
            provenance=(
                RequirementOutcomeProvenance(
                    requirement_key=DELIVERY_REQUIREMENT_KEY,
                    raw_text=DELIVERY_LABEL,
                    experience_codes=(DELIVERY_CODE,),
                ),
                RequirementOutcomeProvenance(
                    requirement_key=FOUNDATION_REQUIREMENT_KEY,
                    raw_text=FOUNDATION_LABEL,
                    experience_codes=(FOUNDATION_CODE,),
                ),
            ),
        ),
    )


def test_shared_entity_outcomes_merge_before_completion_and_reverse_only_their_event(
    acceptance_database_url: str,
) -> None:
    # Given
    user_id = uuid4()
    target_by = datetime.now(UTC) + timedelta(days=30)
    snapshot_reader = StaticSnapshotReader(snapshot=shared_entity_snapshot())
    with TestClient(application(acceptance_database_url, user_id)) as client:
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
    assert goal_response.status_code == 201
    goal_id = UUID(goal_response.json()["goal_id"])
    initial_request_id = anyio.run(
        enqueue_recompute,
        acceptance_database_url,
        user_id,
        2,
        goal_id,
        target_by,
    )
    initial_finalization = anyio.run(
        process_one_recompute, acceptance_database_url, snapshot_reader
    )
    assert initial_finalization.request_id == initial_request_id
    assert initial_finalization.proposal_id is not None

    # When
    with TestClient(application(acceptance_database_url, user_id)) as client:
        roadmap_response = client.post(
            "/api/v1/roadmaps",
            json={
                "expected_profile_version": 2,
                "goal_id": str(goal_id),
                "proposal_id": str(initial_finalization.proposal_id),
                "title": "Merged outcome roadmap",
            },
        )
        assert roadmap_response.status_code == 201
        roadmap_id = UUID(roadmap_response.json()["roadmap_id"])
        activation_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}",
            json={
                "operation": "ACTIVATE",
                "expected_profile_version": 2,
                "expected_roadmap_version": 1,
            },
        )
        detail_response = client.get(f"/api/v1/roadmaps/{roadmap_id}")
        assert detail_response.status_code == 200
        assert detail_response.json()["release_id"] == RELEASE_ID
        step_id = UUID(detail_response.json()["steps"][0]["step_id"])
        started_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}/steps/{step_id}",
            json={
                "state": "IN_PROGRESS",
                "expected_profile_version": 2,
                "expected_roadmap_version": 2,
            },
        )
        completed_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}/steps/{step_id}",
            json={
                "state": "COMPLETED",
                "expected_profile_version": 3,
                "expected_roadmap_version": 3,
            },
        )

    assert activation_response.status_code == 200
    assert started_response.status_code == 200
    assert completed_response.status_code == 200
    assert anyio.run(completion_capabilities, acceptance_database_url, step_id) == (
        DerivedCompletionCapability(
            raw_text=DELIVERY_LABEL,
            entity_id=CAPABILITY_ENTITY_ID,
            requirement_keys=(DELIVERY_REQUIREMENT_KEY, FOUNDATION_REQUIREMENT_KEY),
            experience_codes=(DELIVERY_CODE, FOUNDATION_CODE),
            provenance=(
                (DELIVERY_REQUIREMENT_KEY, DELIVERY_LABEL, (DELIVERY_CODE,)),
                (FOUNDATION_REQUIREMENT_KEY, FOUNDATION_LABEL, (FOUNDATION_CODE,)),
            ),
            lifecycle="ACTIVE",
        ),
    )
    follow_up_finalization = anyio.run(
        process_one_recompute, acceptance_database_url, snapshot_reader
    )

    with TestClient(application(acceptance_database_url, user_id)) as client:
        analysis_response = client.get(f"/api/v1/analyses/{follow_up_finalization.analysis_id}")
        manual_response = client.post(
            "/api/v1/me/capabilities",
            json={
                "expected_profile_version": 4,
                "category": "MANUAL",
                "raw_text": DELIVERY_LABEL,
                "entity_id": CAPABILITY_ENTITY_ID,
                "details": {"experience_codes": []},
            },
        )
        manual_capability_id = UUID(manual_response.json()["capability_id"])
        reversal_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}/steps/{step_id}",
            json={
                "state": "IN_PROGRESS",
                "expected_profile_version": manual_response.json()["profile_version"],
                "expected_roadmap_version": completed_response.json()["roadmap_version"],
            },
        )
        manual_detail_response = client.get(f"/api/v1/me/capabilities/{manual_capability_id}")

    # Then
    assert analysis_response.status_code == 200
    assert {
        requirement["requirement_key"]: requirement["status"]
        for requirement in analysis_response.json()["results"]["requirements"]
    } == {
        DELIVERY_REQUIREMENT_KEY: "SATISFIED",
        FOUNDATION_REQUIREMENT_KEY: "SATISFIED",
    }
    assert manual_response.status_code == 201
    assert reversal_response.status_code == 200
    assert manual_detail_response.status_code == 200
    assert manual_detail_response.json()["lifecycle"] == "ACTIVE"
    assert manual_detail_response.json()["source_completion_event_id"] is None
    assert anyio.run(completion_capabilities, acceptance_database_url, step_id)[0].lifecycle == "REVOKED"

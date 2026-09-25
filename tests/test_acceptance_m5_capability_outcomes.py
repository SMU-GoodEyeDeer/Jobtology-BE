from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import anyio
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_acceptance_m5_lifecycle import _application
from test_acceptance_m5_worker import (
    CAPABILITY_ENTITY_ID,
    CAPABILITY_LABEL,
    EXPERIENCE_CODE,
    RELEASE_ID,
    StaticSnapshotReader,
    _snapshot,
    process_recompute,
)

from jobtology_be.infrastructure.persistence.contracts import (
    JsonValue,
    RecomputeFinalization,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    recompute_contexts,
    recompute_requests,
    step_completion_inheritances,
    user_capabilities,
)
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.workers.context import JsonRecomputeContextReader, RecomputeContextDocument
from jobtology_be.workers.recompute import LeasedRecomputeWorker, RecomputeWorker

pytest_plugins = ("test_acceptance_m5_lifecycle",)


@dataclass(frozen=True, slots=True)
class DerivedCapability:
    raw_text: str
    entity_id: str | None
    experience_codes: tuple[str, ...]
    lifecycle: str

    def context_payload(self) -> Mapping[str, JsonValue]:
        return {
            "raw_text": self.raw_text,
            "entity_id": self.entity_id,
            "experience_codes": list(self.experience_codes),
        }


async def _derived_capability(
    database_url: str, step_id: UUID
) -> DerivedCapability | None:
    database = Database.create(database_url)
    try:
        async with database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(
                            user_capabilities.c.raw_text,
                            user_capabilities.c.entity_id,
                            user_capabilities.c.details,
                            user_capabilities.c.lifecycle,
                        )
                        .join(
                            step_completion_inheritances,
                            user_capabilities.c.source_completion_event_id
                            == step_completion_inheritances.c.original_completion_event_id,
                        )
                        .where(step_completion_inheritances.c.step_id == step_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        details = row["details"]
        return DerivedCapability(
            raw_text=row["raw_text"],
            entity_id=row["entity_id"],
            experience_codes=tuple(details.get("experience_codes", ())),
            lifecycle=row["lifecycle"],
        )
    finally:
        await database.dispose()


async def _pending_context(
    database_url: str, user_id: UUID, profile_version: int
) -> Mapping[str, JsonValue]:
    database = Database.create(database_url)
    try:
        async with database.sessions() as session:
            row = (
                await session.execute(
                    select(recompute_requests.c.state, recompute_contexts.c.payload)
                    .join(
                        recompute_contexts,
                        recompute_contexts.c.request_id == recompute_requests.c.id,
                    )
                    .where(
                        recompute_requests.c.user_id == user_id,
                        recompute_requests.c.profile_version == profile_version,
                    )
                )
            ).one_or_none()
        assert row is not None
        assert row.state == "PENDING"
        return row.payload
    finally:
        await database.dispose()


async def _process_pending_recompute(database_url: str) -> RecomputeFinalization:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)
    try:
        worker = LeasedRecomputeWorker(
            claimer=store,
            processor=RecomputeWorker(
                context_reader=JsonRecomputeContextReader(payload_reader=store),
                snapshot_reader=StaticSnapshotReader(snapshot=_snapshot()),
                finalizer=store,
            ),
            failure_finalizer=store,
        )
        finalizations = await worker.process_once(limit=1)
        assert len(finalizations) == 1
        return finalizations[0]
    finally:
        await database.dispose()


def test_step_outcome_creates_a_resolved_capability_for_a_follow_up_recompute(
    acceptance_database_url: str,
) -> None:
    # Given
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
    assert goal_response.status_code == 201
    goal_id = UUID(goal_response.json()["goal_id"])
    initial_publication = anyio.run(
        process_recompute,
        acceptance_database_url,
        user_id,
        2,
        goal_id,
        (),
        target_by,
    )

    with TestClient(_application(acceptance_database_url, user_id)) as client:
        roadmap_response = client.post(
            "/api/v1/roadmaps",
            json={
                "expected_profile_version": 2,
                "goal_id": str(goal_id),
                "proposal_id": str(initial_publication.proposal_id),
                "title": "Capability outcome roadmap",
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
    active_capability = anyio.run(_derived_capability, acceptance_database_url, step_id)
    assert active_capability == DerivedCapability(
        raw_text=CAPABILITY_LABEL,
        entity_id=CAPABILITY_ENTITY_ID,
        experience_codes=(EXPERIENCE_CODE,),
        lifecycle="ACTIVE",
    )
    assert active_capability is not None
    follow_up_context = anyio.run(_pending_context, acceptance_database_url, user_id, 4)
    assert follow_up_context["profile_version"] == 4
    assert follow_up_context["goal_id"] == str(goal_id)
    assert follow_up_context["snapshot_selection"] == {
        "source": "local_json",
        "occupation_id": "BACKEND_DEVELOPER",
        "basis_version": "reviewed-v1",
        "release_id": RELEASE_ID,
    }
    assert follow_up_context["capabilities"] == [active_capability.context_payload()]
    follow_up_document = RecomputeContextDocument.model_validate(follow_up_context)
    assert follow_up_document.reference_at > datetime(2026, 9, 22, tzinfo=UTC)
    assert follow_up_document.planning_started_at > datetime(2026, 9, 22, tzinfo=UTC)
    assert follow_up_document.calendar
    assert follow_up_document.calendar[0].starts_at >= follow_up_document.planning_started_at
    follow_up_finalization = anyio.run(_process_pending_recompute, acceptance_database_url)
    with TestClient(_application(acceptance_database_url, user_id)) as client:
        analysis_response = client.get(f"/api/v1/analyses/{follow_up_finalization.analysis_id}")
        reversal_response = client.patch(
            f"/api/v1/roadmaps/{roadmap_id}/steps/{step_id}",
            json={
                "state": "IN_PROGRESS",
                "expected_profile_version": 4,
                "expected_roadmap_version": 4,
            },
        )

    assert analysis_response.status_code == 200
    assert analysis_response.json()["results"]["requirements"][0]["status"] == "SATISFIED"
    assert reversal_response.status_code == 200
    assert anyio.run(_derived_capability, acceptance_database_url, step_id) == DerivedCapability(
        raw_text=CAPABILITY_LABEL,
        entity_id=CAPABILITY_ENTITY_ID,
        experience_codes=(EXPERIENCE_CODE,),
        lifecycle="REVOKED",
    )
    reversal_context = anyio.run(_pending_context, acceptance_database_url, user_id, 5)
    assert reversal_context["capabilities"] == []
    reversal_document = RecomputeContextDocument.model_validate(reversal_context)
    assert reversal_document.calendar[0].starts_at >= reversal_document.planning_started_at
    reversal_finalization = anyio.run(_process_pending_recompute, acceptance_database_url)
    assert reversal_finalization.request_id

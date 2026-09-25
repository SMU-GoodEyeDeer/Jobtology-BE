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
    StaticSnapshotReader,
    _snapshot,
    process_recompute,
)

from jobtology_be.infrastructure.persistence.contracts import (
    CapabilityDelete,
    CapabilityMutation,
    GoalMutation,
    ProfileMutation,
    RoutePreferenceMutation,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    recompute_contexts,
    recompute_requests,
)
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.workers.context import JsonRecomputeContextReader, RecomputeContextDocument
from jobtology_be.workers.recompute import LeasedRecomputeWorker, RecomputeWorker

pytest_plugins = ("test_acceptance_m5_lifecycle",)


async def _pending_context(
    database: Database, user_id: UUID, profile_version: int
) -> RecomputeContextDocument:
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
        ).one()
    assert row.state == "PENDING"
    return RecomputeContextDocument.model_validate(row.payload)


async def _process_one(store: PostgresApplicationStore) -> None:
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
    assert finalizations[0].proposal_id is not None


async def _exercise_configured_mutations(
    database_url: str, user_id: UUID, goal_id: UUID, target_by: datetime
) -> None:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)
    try:
        capability = await store.mutate_capability(
            CapabilityMutation(
                user_id=user_id,
                expected_profile_version=2,
                capability_id=None,
                category="SELF_REPORTED",
                raw_text=CAPABILITY_LABEL,
                entity_id=CAPABILITY_ENTITY_ID,
                proficiency=None,
                details={"experience_codes": [EXPERIENCE_CODE]},
            )
        )
        assert capability.profile_version == 3
        capability_context = await _pending_context(database, user_id, 3)
        assert capability_context.capabilities[0].entity_id == CAPABILITY_ENTITY_ID
        assert capability_context.completeness.entities_complete
        assert capability_context.completeness.experience_complete_entity_ids == frozenset(
            {CAPABILITY_ENTITY_ID}
        )
        await _process_one(store)

        preferences = await store.set_route_preferences(
            RoutePreferenceMutation(
                user_id=user_id,
                expected_profile_version=3,
                available_hours_per_week=3,
                availability_source="SELF_REPORTED",
                budget_mode="REGULAR",
                max_out_of_pocket_krw=None,
                fastest_path=True,
                needs_portfolio=True,
                career_switch=False,
            )
        )
        assert preferences.version == 4
        preference_context = await _pending_context(database, user_id, 4)
        assert preference_context.constraints.available_hours_per_week == 3
        assert preference_context.constraints.fastest_path
        assert preference_context.constraints.needs_portfolio
        assert preference_context.calendar[0].starts_at >= preference_context.planning_started_at
        await _process_one(store)

        refreshed_target_by = target_by + timedelta(days=1)
        goal = await store.mutate_goal(
            GoalMutation(
                user_id=user_id,
                expected_profile_version=4,
                goal_id=goal_id,
                goal_mode="TARGETED",
                occupation_id="BACKEND_DEVELOPER",
                target_by=refreshed_target_by,
                timezone="UTC",
                original_time_phrase="within a month",
                status="ACTIVE",
            )
        )
        assert goal.goal_id == goal_id
        goal_context = await _pending_context(database, user_id, 5)
        assert goal_context.constraints.target_by == refreshed_target_by
        assert goal_context.snapshot_selection.occupation_id == "BACKEND_DEVELOPER"
        await _process_one(store)

        profile = await store.mutate_profile(
            ProfileMutation(
                user_id=user_id,
                expected_version=5,
                major_raw="Computer Science",
                major_concept_id="computer-science",
                year=3,
                enrollment_status="ENROLLED",
                expected_graduation_on=None,
            )
        )
        assert profile.version == 6
        profile_context = await _pending_context(database, user_id, 6)
        assert profile_context.profile_version == 6
        assert profile_context.capabilities[0].entity_id == CAPABILITY_ENTITY_ID
        await _process_one(store)

        deleted = await store.delete_capability(
            CapabilityDelete(
                user_id=user_id,
                capability_id=capability.capability_id,
                expected_profile_version=6,
            )
        )
        assert deleted.version == 7
        deleted_context = await _pending_context(database, user_id, 7)
        assert deleted_context.capabilities == ()
        assert not deleted_context.completeness.entities_complete
        await _process_one(store)

        switched_goal = await store.mutate_goal(
            GoalMutation(
                user_id=user_id,
                expected_profile_version=7,
                goal_id=goal_id,
                goal_mode="TARGETED",
                occupation_id="DATA_SCIENTIST",
                target_by=refreshed_target_by,
                timezone="UTC",
                original_time_phrase="within a month",
                status="ACTIVE",
            )
        )
        assert switched_goal.goal_id == goal_id
        async with database.sessions() as session:
            failed = (
                await session.execute(
                    select(recompute_requests.c.state, recompute_requests.c.error_code).where(
                        recompute_requests.c.user_id == user_id,
                        recompute_requests.c.profile_version == 8,
                    )
                )
            ).one()
        assert failed.state == "FAILED"
        assert failed.error_code == "MISSING_RECOMPUTE_CONTEXT"
    finally:
        await database.dispose()


def test_configured_mutations_refresh_recompute_context_without_stale_snapshot_reuse(
    acceptance_database_url: str,
) -> None:
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
                "original_time_phrase": "within a month",
            },
        )
    assert goal_response.status_code == 201
    goal_id = UUID(goal_response.json()["goal_id"])
    _ = anyio.run(
        process_recompute,
        acceptance_database_url,
        user_id,
        2,
        goal_id,
        (),
        target_by,
    )

    anyio.run(_exercise_configured_mutations, acceptance_database_url, user_id, goal_id, target_by)

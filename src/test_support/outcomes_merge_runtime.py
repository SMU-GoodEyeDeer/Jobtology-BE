from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI
from sqlalchemy import select

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.infrastructure.persistence.contracts import (
    JsonValue,
    RecomputeFinalization,
    RecomputeRequestCreate,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    step_completion_inheritances,
    user_capabilities,
    user_state_events,
)
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.main import create_app
from jobtology_be.settings import Settings
from jobtology_be.workers.context import JsonRecomputeContextReader
from jobtology_be.workers.recompute import LeasedRecomputeWorker, RecomputeWorker
from test_support.outcomes_merge_data import (
    CAPABILITY_ENTITY_ID,
    REFERENCE_AT,
    RELEASE_ID,
    DerivedCompletionCapability,
    StaticSnapshotReader,
)


@dataclass(frozen=True, slots=True)
class StaticIdentityProvider:
    user_id: UUID

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


def application(database_url: str, user_id: UUID) -> FastAPI:
    return create_app(
        Settings(database_url=database_url, enable_fixtures=False),
        dependencies=ApiDependencies(identity_provider=StaticIdentityProvider(user_id=user_id)),
    )


def _context_payload(
    user_id: UUID, profile_version: int, goal_id: UUID, target_by: datetime
) -> Mapping[str, JsonValue]:
    return {
        "user_id": str(user_id),
        "profile_version": profile_version,
        "goal_id": str(goal_id),
        "snapshot_selection": {
            "occupation_id": "BACKEND_DEVELOPER",
            "basis_version": "reviewed-v1",
            "release_id": RELEASE_ID,
        },
        "capabilities": [],
        "completeness": {
            "entities_complete": True,
            "experience_complete_entity_ids": [CAPABILITY_ENTITY_ID],
        },
        "constraints": {
            "target_by": target_by.isoformat(),
            "available_hours_per_week": 8,
        },
        "reference_at": REFERENCE_AT.isoformat(),
        "planning_started_at": REFERENCE_AT.isoformat(),
        "calendar": [
            {
                "starts_at": REFERENCE_AT.isoformat(),
                "ends_at": (REFERENCE_AT + timedelta(hours=1)).isoformat(),
                "capacity_week_key": "2026-W39",
            }
        ],
        "candidate_availability": [],
        "solver_settings": {"time_limit_seconds": 20.0},
    }


async def enqueue_recompute(
    database_url: str,
    user_id: UUID,
    profile_version: int,
    goal_id: UUID,
    target_by: datetime,
) -> UUID:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)
    trigger_event_id = uuid4()
    try:
        async with database.sessions.begin() as session:
            await session.execute(
                user_state_events.insert().values(
                    id=trigger_event_id,
                    user_id=user_id,
                    aggregate_id=user_id,
                    aggregate_type="PROFILE",
                    version=profile_version,
                    kind="ANALYSIS_REQUESTED",
                    payload={"goal_id": str(goal_id)},
                )
            )
        request = await store.create_recompute_request(
            RecomputeRequestCreate(
                user_id=user_id,
                profile_version=profile_version,
                trigger_event_id=trigger_event_id,
                dedupe_key=f"outcomes-merge:{trigger_event_id}",
                payload={"source": "outcomes-merge"},
                context=_context_payload(user_id, profile_version, goal_id, target_by),
            )
        )
        assert request.state == "PENDING"
        return request.request_id
    finally:
        await database.dispose()


async def process_one_recompute(
    database_url: str, snapshot_reader: StaticSnapshotReader
) -> RecomputeFinalization:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)
    try:
        worker = LeasedRecomputeWorker(
            claimer=store,
            processor=RecomputeWorker(
                context_reader=JsonRecomputeContextReader(payload_reader=store),
                snapshot_reader=snapshot_reader,
                finalizer=store,
            ),
            failure_finalizer=store,
        )
        finalizations = await worker.process_once(limit=1)
        assert len(finalizations) == 1
        return finalizations[0]
    finally:
        await database.dispose()


async def completion_capabilities(
    database_url: str, step_id: UUID
) -> tuple[DerivedCompletionCapability, ...]:
    database = Database.create(database_url)
    try:
        async with database.sessions() as session:
            rows = (
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
                .all()
            )
        return tuple(
            DerivedCompletionCapability(
                raw_text=row["raw_text"],
                entity_id=row["entity_id"],
                requirement_keys=tuple(row["details"].get("outcome_requirement_keys", ())),
                experience_codes=tuple(row["details"].get("experience_codes", ())),
                provenance=tuple(
                    (
                        item["requirement_key"],
                        item["raw_text"],
                        tuple(item["experience_codes"]),
                    )
                    for item in row["details"].get("outcome_provenance", ())
                ),
                lifecycle=row["lifecycle"],
            )
            for row in rows
        )
    finally:
        await database.dispose()

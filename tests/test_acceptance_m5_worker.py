from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import insert, select

from jobtology_be.corpus.snapshot import (
    PublishedCorpusSnapshot,
    PublishedSnapshotSelection,
)
from jobtology_be.infrastructure.persistence.contracts import (
    JsonValue,
    RecomputeRequestCreate,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    step_completion_inheritances,
    user_state_events,
)
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.modules.analyses.editorial_models import (
    EditorialReleaseMetadata,
    EditorialRequirement,
    ReleaseState,
    RequirementNecessity,
)
from jobtology_be.modules.profiles.normalizer import CapabilityCatalogEntry
from jobtology_be.planning.candidate_models import ActivityTemplate, KnownKrwCost
from jobtology_be.workers.context import JsonRecomputeContextReader
from jobtology_be.workers.recompute import LeasedRecomputeWorker, RecomputeWorker

REFERENCE_AT = datetime(2026, 9, 22, tzinfo=UTC)
RELEASE_ID = "release-reviewed-v1"
REQUIREMENT_KEY = "api-requirement"
CAPABILITY_ENTITY_ID = "python-api"
CAPABILITY_LABEL = "Python API"
EXPERIENCE_CODE = "DELIVERED"


@dataclass(frozen=True, slots=True)
class WorkerPublication:
    recompute_request_id: UUID
    analysis_id: UUID
    proposal_id: UUID


@dataclass(frozen=True, slots=True)
class StaticSnapshotReader:
    snapshot: PublishedCorpusSnapshot

    async def get_snapshot(self, _: PublishedSnapshotSelection) -> PublishedCorpusSnapshot:
        return self.snapshot


def _snapshot() -> PublishedCorpusSnapshot:
    return PublishedCorpusSnapshot(
        occupation_id="BACKEND_DEVELOPER",
        basis_version="reviewed-v1",
        release=EditorialReleaseMetadata(
            release_id=RELEASE_ID,
            state=ReleaseState.PUBLISHED,
            reviewed_at=REFERENCE_AT,
        ),
        is_fixture=False,
        capability_entries=(
            CapabilityCatalogEntry(
                entity_id=CAPABILITY_ENTITY_ID,
                aliases=frozenset({CAPABILITY_LABEL}),
            ),
        ),
        allowed_experience_codes=frozenset({EXPERIENCE_CODE}),
        requirements=(
            EditorialRequirement(
                requirement_key=REQUIREMENT_KEY,
                label=CAPABILITY_LABEL,
                necessity=RequirementNecessity.REQUIRED,
                entity_id=CAPABILITY_ENTITY_ID,
                required_experience_codes=frozenset({EXPERIENCE_CODE}),
                support_refs=frozenset({"review:api"}),
            ),
        ),
        templates=(
            ActivityTemplate(
                action_id="api-project",
                revision=1,
                title="API project",
                estimated_hours=1,
                outcome_requirement_keys=frozenset({REQUIREMENT_KEY}),
                prerequisite_action_ids=(),
                completion_criteria=("Publish an API",),
                support_refs=frozenset({"template:api-project"}),
                cost=KnownKrwCost(krw=0),
                is_foundational=False,
            ),
        ),
    )


def _context_payload(
    user_id: UUID,
    profile_version: int,
    goal_id: UUID,
    capabilities: tuple[Mapping[str, JsonValue], ...] = (),
    target_by: datetime | None = None,
) -> Mapping[str, JsonValue]:
    resolved_target_by = REFERENCE_AT + timedelta(days=7) if target_by is None else target_by
    return {
        "user_id": str(user_id),
        "profile_version": profile_version,
        "goal_id": str(goal_id),
        "snapshot_selection": {
            "occupation_id": "BACKEND_DEVELOPER",
            "basis_version": "reviewed-v1",
            "release_id": RELEASE_ID,
        },
        "capabilities": list(capabilities),
        "completeness": {
            "entities_complete": True,
            "experience_complete_entity_ids": [CAPABILITY_ENTITY_ID],
        },
        "constraints": {
            "target_by": resolved_target_by.isoformat(),
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
    capabilities: tuple[Mapping[str, JsonValue], ...] = (),
    target_by: datetime | None = None,
) -> UUID:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)
    trigger_event_id = uuid4()
    try:
        async with database.sessions.begin() as session:
            await session.execute(
                insert(user_state_events).values(
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
                dedupe_key=f"recompute:{trigger_event_id}",
                payload={"source": "m5-acceptance"},
                context=_context_payload(
                    user_id,
                    profile_version,
                    goal_id,
                    capabilities,
                    target_by,
                ),
            )
        )
        assert request.state == "PENDING"
        return request.request_id
    finally:
        await database.dispose()


async def process_recompute(
    database_url: str,
    user_id: UUID,
    profile_version: int,
    goal_id: UUID,
    capabilities: tuple[Mapping[str, JsonValue], ...] = (),
    target_by: datetime | None = None,
) -> WorkerPublication:
    request_id = await enqueue_recompute(
        database_url, user_id, profile_version, goal_id, capabilities, target_by
    )
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
        finalization = finalizations[0]
        assert finalization.request_id == request_id
        assert finalization.proposal_id is not None
        return WorkerPublication(
            recompute_request_id=finalization.request_id,
            analysis_id=finalization.analysis_id,
            proposal_id=finalization.proposal_id,
        )
    finally:
        await database.dispose()


async def has_completion_lineage(database_url: str, step_id: UUID) -> bool:
    database = Database.create(database_url)
    try:
        async with database.sessions() as session:
            completion_event_id = await session.scalar(
                select(step_completion_inheritances.c.original_completion_event_id).where(
                    step_completion_inheritances.c.step_id == step_id
                )
            )
        return completion_event_id is not None
    finally:
        await database.dispose()

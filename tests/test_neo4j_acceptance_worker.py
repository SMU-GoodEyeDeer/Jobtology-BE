from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import environ
from typing import assert_never
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, update

from jobtology_be.infrastructure.persistence.contracts import (
    JsonValue,
    RecomputeRequestCreate,
    UserCreate,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    outbox_jobs,
    recompute_requests,
    user_state_events,
)
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.settings import CorpusSource, Settings
from jobtology_be.workers.context import RecomputeContextDocument
from jobtology_be.workers.main import WorkerSettings, run_once
from jobtology_be.workers.recompute import RecomputeFailureCode

pytest_plugins = ("test_acceptance_m5_lifecycle",)

_LIVE_ACCEPTANCE_ENV = "JOBTOLOGY_NEO4J_LIVE_ACCEPTANCE"


@dataclass(frozen=True, slots=True)
class _QueuedContext:
    user_id: UUID
    profile_version: int
    context: Mapping[str, JsonValue]


def _native_worker_settings(database_url: str) -> WorkerSettings:
    configured = Settings()
    if configured.db_link is None or configured.db_password is None:
        pytest.skip("typed runtime settings do not contain Neo4j credentials")
    return WorkerSettings(
        database_url=database_url,
        corpus_source="neo4j_query_api",
        corpus_snapshot_path=None,
        db_link=configured.db_link,
        db_password=configured.db_password,
        db_protocol=configured.db_protocol,
        worker_batch_limit=1,
    )


def _serialized_context(
    user_id: UUID, profile_version: int, source: CorpusSource
) -> dict[str, JsonValue]:
    reference_at = datetime.now(UTC)
    document = RecomputeContextDocument.model_validate(
        {
            "user_id": user_id,
            "profile_version": profile_version,
            "goal_id": uuid4(),
            "snapshot_selection": {
                "occupation_id": "PERSISTED_SOURCE_TEST",
                "basis_version": "source-v1",
                "release_id": "source-release-v1",
                "source": source,
            },
            "capabilities": (),
            "completeness": {"entities_complete": True},
            "constraints": {
                "target_by": reference_at + timedelta(days=1),
                "available_hours_per_week": 1,
            },
            "reference_at": reference_at,
            "planning_started_at": reference_at,
            "calendar": (),
        }
    )
    match source:
        case "local_json":
            return document.model_dump(
                mode="json", exclude={"snapshot_selection": {"source"}}
            )
        case "neo4j_query_api":
            return document.model_dump(mode="json")
        case unreachable:
            assert_never(unreachable)


async def _enqueue(database_url: str, queued: _QueuedContext) -> UUID:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)
    trigger_event_id = uuid4()
    try:
        await store.create_user(UserCreate(user_id=queued.user_id))
        async with database.sessions.begin() as session:
            _ = await session.execute(
                insert(user_state_events).values(
                    id=trigger_event_id,
                    user_id=queued.user_id,
                    aggregate_id=queued.user_id,
                    aggregate_type="PROFILE",
                    version=queued.profile_version,
                    kind="ANALYSIS_REQUESTED",
                    payload={},
                )
            )
        request = await store.create_recompute_request(
            RecomputeRequestCreate(
                user_id=queued.user_id,
                profile_version=queued.profile_version,
                trigger_event_id=trigger_event_id,
                dedupe_key=f"persisted-source:{trigger_event_id}",
                payload={"source": "neo4j-acceptance"},
                context=queued.context,
            )
        )
        assert request.state == "PENDING"
        return request.request_id
    finally:
        await database.dispose()


async def _states(
    database_url: str, request_ids: tuple[UUID, ...]
) -> dict[UUID, tuple[str, str | None]]:
    database = Database.create(database_url)
    try:
        async with database.sessions() as session:
            rows = (
                await session.execute(
                    select(
                        recompute_requests.c.id,
                        recompute_requests.c.state,
                        recompute_requests.c.error_code,
                    ).where(recompute_requests.c.id.in_(request_ids))
                )
            ).all()
        return {row.id: (row.state, row.error_code) for row in rows}
    finally:
        await database.dispose()


@pytest.mark.skipif(
    environ.get(_LIVE_ACCEPTANCE_ENV) != "1",
    reason=f"set {_LIVE_ACCEPTANCE_ENV}=1 for the authorized Neo4j worker check",
)
@pytest.mark.anyio
async def test_native_worker_with_limit_one_skips_older_legacy_context_and_terminalizes_native_context(
    acceptance_database_url: str,
) -> None:
    # Given: an older legacy job precedes an eligible native job in the ready queue.
    user_id = uuid4()
    legacy_payload = _serialized_context(user_id, 1, "local_json")
    native_payload = _serialized_context(user_id, 2, "neo4j_query_api")
    legacy_request_id = await _enqueue(
        acceptance_database_url,
        _QueuedContext(user_id=user_id, profile_version=1, context=legacy_payload),
    )
    native_request_id = await _enqueue(
        acceptance_database_url,
        _QueuedContext(user_id=user_id, profile_version=2, context=native_payload),
    )
    database = Database.create(acceptance_database_url)
    try:
        async with database.sessions.begin() as session:
            _ = await session.execute(
                update(outbox_jobs)
                .where(outbox_jobs.c.recompute_request_id == legacy_request_id)
                .values(available_at=datetime.now(UTC) - timedelta(days=1))
            )
    finally:
        await database.dispose()

    # When: the current configured worker claims one native-only job with no local snapshot path.
    finalizations = await run_once(_native_worker_settings(acceptance_database_url))
    states = await _states(acceptance_database_url, (legacy_request_id, native_request_id))

    # Then: the ineligible older legacy job cannot consume the sole native worker slot.
    assert finalizations == ()
    assert states[legacy_request_id] == ("PENDING", None)
    assert states[native_request_id] == (
        "FAILED",
        RecomputeFailureCode.NATIVE_SOURCE_UNSUPPORTED.value,
    )


@pytest.mark.skipif(
    environ.get(_LIVE_ACCEPTANCE_ENV) != "1",
    reason=f"set {_LIVE_ACCEPTANCE_ENV}=1 for the authorized Neo4j worker check",
)
@pytest.mark.anyio
async def test_native_worker_reclaims_expired_native_lease_and_terminalizes_it(
    acceptance_database_url: str,
) -> None:
    # Given: an eligible native request has an expired outbox lease.
    user_id = uuid4()
    native_request_id = await _enqueue(
        acceptance_database_url,
        _QueuedContext(
            user_id=user_id,
            profile_version=1,
            context=_serialized_context(user_id, 1, "neo4j_query_api"),
        ),
    )
    database = Database.create(acceptance_database_url)
    try:
        async with database.sessions.begin() as session:
            _ = await session.execute(
                update(outbox_jobs)
                .where(outbox_jobs.c.recompute_request_id == native_request_id)
                .values(
                    state="LEASED",
                    lease_until=datetime.now(UTC) - timedelta(days=1),
                )
            )
    finally:
        await database.dispose()

    # When: the native worker processes its one available claim.
    finalizations = await run_once(_native_worker_settings(acceptance_database_url))
    states = await _states(acceptance_database_url, (native_request_id,))

    # Then: the expired eligible lease is reclaimed and fails closed for unsupported planning.
    assert finalizations == ()
    assert states[native_request_id] == (
        "FAILED",
        RecomputeFailureCode.NATIVE_SOURCE_UNSUPPORTED.value,
    )

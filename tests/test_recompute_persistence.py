from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from os import environ
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import anyio
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from jobtology_be.infrastructure.persistence.contracts import (
    JsonValue,
    PersistenceConflictError,
    RecomputeRequestCreate,
    UserCreate,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import user_state_events
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore

ACCEPTANCE_DATABASE_URL_ENV: Final = "JOBTOLOGY_ACCEPTANCE_DATABASE_URL"
REFERENCE_AT: Final = datetime(2026, 9, 22, 12, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class IsolatedDatabase:
    admin_url: str
    database_url: str
    database_name: str


def _base_url() -> str:
    database_url = environ.get(ACCEPTANCE_DATABASE_URL_ENV)
    if database_url is None:
        pytest.skip(f"Set {ACCEPTANCE_DATABASE_URL_ENV} to run PostgreSQL acceptance tests")
    return database_url


async def _provision_database(base_url: str) -> IsolatedDatabase:
    url = make_url(base_url)
    database_name = f"jobtology_recompute_{uuid4().hex}"
    admin_url = url.set(database="postgres").render_as_string(hide_password=False)
    database_url = url.set(database=database_name).render_as_string(hide_password=False)
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        await engine.dispose()
    return IsolatedDatabase(
        admin_url=admin_url,
        database_url=database_url,
        database_name=database_name,
    )


async def _drop_database(database: IsolatedDatabase) -> None:
    engine = create_async_engine(database.admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{database.database_name}" WITH (FORCE)'))
    finally:
        await engine.dispose()


def _upgrade_database(database_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


@pytest.fixture
def recompute_database_url() -> Iterator[str]:
    database = anyio.run(_provision_database, _base_url())
    try:
        _upgrade_database(database.database_url)
        yield database.database_url
    finally:
        anyio.run(_drop_database, database)


def _context_payload(
    user_id: UUID,
    goal_id: UUID,
    *,
    corpus_source: str | None = None,
) -> Mapping[str, JsonValue]:
    snapshot_selection: dict[str, JsonValue] = {
        "occupation_id": "BACKEND_DEVELOPER",
        "basis_version": "reviewed-v1",
        "release_id": "release-reviewed-v1",
    }
    if corpus_source is not None:
        snapshot_selection["source"] = corpus_source
    return {
        "user_id": str(user_id),
        "profile_version": 1,
        "goal_id": str(goal_id),
        "snapshot_selection": snapshot_selection,
        "capabilities": [],
        "completeness": {
            "entities_complete": True,
            "experience_complete_entity_ids": [],
        },
        "constraints": {
            "target_by": "2026-10-22T12:00:00+00:00",
            "available_hours_per_week": 4,
        },
        "reference_at": REFERENCE_AT.isoformat(),
        "planning_started_at": REFERENCE_AT.isoformat(),
        "calendar": [
            {
                "starts_at": "2026-09-22T12:00:00+00:00",
                "ends_at": "2026-09-22T13:00:00+00:00",
                "capacity_week_key": "2026-W39",
            }
        ],
        "candidate_availability": [],
        "solver_settings": {"time_limit_seconds": 20.0},
    }


async def _exercise_recompute_lifecycle(database_url: str) -> None:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)
    user_id = uuid4()
    goal_id = uuid4()
    trigger_event_id = uuid4()
    try:
        await store.create_user(UserCreate(user_id=user_id))
        async with database.sessions.begin() as session:
            await session.execute(
                insert(user_state_events).values(
                    id=trigger_event_id,
                    user_id=user_id,
                    aggregate_id=user_id,
                    aggregate_type="PROFILE",
                    version=1,
                    kind="ANALYSIS_REQUESTED",
                    payload={"goal_id": str(goal_id)},
                )
            )
        context = _context_payload(user_id, goal_id)
        request = await store.create_recompute_request(
            RecomputeRequestCreate(
                user_id=user_id,
                profile_version=1,
                trigger_event_id=trigger_event_id,
                dedupe_key=f"recompute:{trigger_event_id}",
                payload={"source": "test"},
                context=context,
            )
        )
        assert request.state == "PENDING"
        async with database.sessions() as session:
            binding = (
                await session.execute(
                    text(
                        "SELECT request.state, job.state, job.recompute_request_id "
                        "FROM recompute_requests AS request "
                        "JOIN recompute_contexts AS context ON context.request_id = request.id "
                        "JOIN outbox_jobs AS job ON job.recompute_request_id = request.id "
                        "WHERE request.id = :request_id"
                    ),
                    {"request_id": request.request_id},
                )
            ).one_or_none()
        assert binding == ("PENDING", "READY", request.request_id)
        work_items = await store.claim_recompute_work(limit=1)
        assert len(work_items) == 1
        work_item = work_items[0]
        assert work_item.request_id == request.request_id
        assert await store.load_recompute_context(work_item) == context

        forged_work_item = work_item.__class__(
            request_id=uuid4(),
            user_id=work_item.user_id,
            profile_version=work_item.profile_version,
            lease=work_item.lease,
        )
        with pytest.raises(PersistenceConflictError):
            await store.fail_recompute(forged_work_item, "SOLVER_TIMEOUT")

        async with database.sessions.begin() as session:
            await session.execute(
                text(
                    "CREATE FUNCTION delay_recompute_failure() RETURNS trigger AS $$ "
                    "BEGIN PERFORM pg_sleep(0.5); RETURN NEW; END; $$ LANGUAGE plpgsql"
                )
            )
            await session.execute(
                text(
                    "CREATE TRIGGER delay_recompute_failure_trigger "
                    "BEFORE UPDATE ON recompute_requests "
                    "FOR EACH ROW EXECUTE FUNCTION delay_recompute_failure()"
                )
            )
            await session.execute(
                text(
                    "UPDATE outbox_jobs "
                    "SET lease_until = clock_timestamp() + interval '250 milliseconds' "
                    "WHERE id = :job_id"
                ),
                {"job_id": work_item.lease.job_id},
            )
        with pytest.raises(PersistenceConflictError):
            await store.fail_recompute(work_item, "SOLVER_TIMEOUT")

        reclaimed_work_items = await store.claim_recompute_work(limit=1)
        assert len(reclaimed_work_items) == 1
        reclaimed_work_item = reclaimed_work_items[0]
        await store.fail_recompute(reclaimed_work_item, "SOLVER_TIMEOUT")
        async with database.sessions() as session:
            request_row = (
                await session.execute(
                    text(
                        "SELECT state, error_code FROM recompute_requests "
                        "WHERE id = :request_id"
                    ),
                    {"request_id": request.request_id},
                )
            ).one()
            job_row = (
                await session.execute(
                    text(
                        "SELECT state, recompute_request_id FROM outbox_jobs "
                        "WHERE id = :job_id"
                    ),
                    {"job_id": reclaimed_work_item.lease.job_id},
                )
            ).one()
        assert request_row == ("FAILED", "SOLVER_TIMEOUT")
        assert job_row == ("COMPLETED", request.request_id)
    finally:
        await database.dispose()


def test_recompute_context_is_immutable_and_worker_finalization_is_request_bound(
    recompute_database_url: str,
) -> None:
    # Given
    database_url = recompute_database_url

    # When / Then
    anyio.run(_exercise_recompute_lifecycle, database_url)


async def _claim_only_explicit_native_contexts(database_url: str) -> None:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)

    async def create_pending_request(corpus_source: str | None) -> UUID:
        user_id = uuid4()
        goal_id = uuid4()
        trigger_event_id = uuid4()
        await store.create_user(UserCreate(user_id=user_id))
        async with database.sessions.begin() as session:
            await session.execute(
                insert(user_state_events).values(
                    id=trigger_event_id,
                    user_id=user_id,
                    aggregate_id=user_id,
                    aggregate_type="PROFILE",
                    version=1,
                    kind="ANALYSIS_REQUESTED",
                    payload={"goal_id": str(goal_id)},
                )
            )
        request = await store.create_recompute_request(
            RecomputeRequestCreate(
                user_id=user_id,
                profile_version=1,
                trigger_event_id=trigger_event_id,
                dedupe_key=f"recompute:{trigger_event_id}",
                payload={"source": "test"},
                context=_context_payload(user_id, goal_id, corpus_source=corpus_source),
            )
        )
        return request.request_id

    try:
        legacy_request_id = await create_pending_request(None)
        native_request_id = await create_pending_request("neo4j_query_api")

        work_items = await store.claim_recompute_work(
            limit=2,
            eligible_corpus_sources=frozenset({"neo4j_query_api"}),
        )

        assert [work_item.request_id for work_item in work_items] == [native_request_id]
        async with database.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT request.id, request.state, job.state, job.attempt_count "
                        "FROM recompute_requests AS request "
                        "JOIN outbox_jobs AS job ON job.recompute_request_id = request.id "
                        "WHERE request.id IN (:legacy_request_id, :native_request_id) "
                        "ORDER BY request.id"
                    ),
                    {
                        "legacy_request_id": legacy_request_id,
                        "native_request_id": native_request_id,
                    },
                )
            ).all()
        state_by_request = {row.id: row[1:] for row in rows}
        assert state_by_request[legacy_request_id] == ("PENDING", "READY", 0)
        assert state_by_request[native_request_id] == ("RUNNING", "LEASED", 1)
    finally:
        await database.dispose()


def test_claim_filter_preserves_untagged_legacy_contexts_for_a_future_local_worker(
    recompute_database_url: str,
) -> None:
    anyio.run(_claim_only_explicit_native_contexts, recompute_database_url)

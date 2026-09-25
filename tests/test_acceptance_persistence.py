from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    GoalMutation,
    IdempotencyAcquire,
    IdempotencyComplete,
    IdempotencyConflictError,
    MissingRecordError,
    PersistenceConflictError,
    ProfileMutation,
    ProfileSnapshot,
    RecomputeRequestCreate,
    UserCreate,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import user_state_events
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore

ACCEPTANCE_DATABASE_URL_ENV: Final = "JOBTOLOGY_ACCEPTANCE_DATABASE_URL"
HEAD_REVISION: Final = "20260922_04"
REQUIRED_TABLES: Final = frozenset(
    {
        "alembic_version",
        "analyses",
        "auth_sessions",
        "google_identities",
        "goals",
        "idempotency_records",
        "oauth_login_attempts",
        "outbox_jobs",
        "profiles",
        "recompute_contexts",
        "recompute_requests",
        "roadmaps",
        "step_completion_inheritances",
        "user_state_events",
        "users",
    }
)


@dataclass(frozen=True, slots=True)
class IsolatedDatabase:
    admin_url: str
    database_url: str
    database_name: str


@dataclass(frozen=True, slots=True)
class SchemaState:
    revision: str
    tables: frozenset[str]


@dataclass(frozen=True, slots=True)
class MutationAttempt:
    profile: ProfileSnapshot | None
    conflict: PersistenceConflictError | None


@dataclass(frozen=True, slots=True)
class ConcurrentProfileMutation:
    store: PostgresApplicationStore
    request: ProfileMutation


@dataclass(frozen=True, slots=True)
class StoreAcceptanceResult:
    conflict_count: int
    cross_user_goal_change_rejected: bool
    other_user_profile_version: int
    successful_profile_versions: tuple[int, ...]


def _acceptance_base_url() -> str:
    database_url = environ.get(ACCEPTANCE_DATABASE_URL_ENV)
    if database_url is None:
        pytest.skip(f"Set {ACCEPTANCE_DATABASE_URL_ENV} to run PostgreSQL acceptance tests")
    return database_url


async def _provision_database(base_url: str) -> IsolatedDatabase:
    url = make_url(base_url)
    database_name = f"jobtology_acceptance_{uuid4().hex}"
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


def _downgrade_database(database_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config, "base")


@pytest.fixture
def acceptance_database_url() -> Iterator[str]:
    database = anyio.run(_provision_database, _acceptance_base_url())
    try:
        _upgrade_database(database.database_url)
        yield database.database_url
    finally:
        anyio.run(_drop_database, database)


async def _schema_state(database_url: str) -> SchemaState:
    database = Database.create(database_url)
    try:
        async with database.engine.connect() as connection:
            tables = frozenset(
                (await connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )).scalars()
            )
            revision = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
    finally:
        await database.dispose()
    return SchemaState(revision=revision, tables=tables)


async def _attempt_profile_mutation(
    attempt: ConcurrentProfileMutation, outcomes: list[MutationAttempt]
) -> None:
    try:
        profile = await attempt.store.mutate_profile(attempt.request)
    except PersistenceConflictError as conflict:
        outcomes.append(MutationAttempt(profile=None, conflict=conflict))
    else:
        outcomes.append(MutationAttempt(profile=profile, conflict=None))


def _profile_mutation(user_id: UUID, expected_version: int, major_raw: str) -> ProfileMutation:
    return ProfileMutation(
        user_id=user_id,
        expected_version=expected_version,
        major_raw=major_raw,
        major_concept_id=None,
        year=None,
        enrollment_status=None,
        expected_graduation_on=None,
    )


async def _exercise_store(database_url: str) -> StoreAcceptanceResult:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)
    owner_id = uuid4()
    other_user_id = uuid4()
    reference_at = datetime(2026, 9, 22, tzinfo=UTC)
    try:
        await store.create_user(UserCreate(user_id=owner_id))
        await store.create_user(UserCreate(user_id=other_user_id))
        outcomes: list[MutationAttempt] = []
        attempt = ConcurrentProfileMutation(
            store=store,
            request=_profile_mutation(owner_id, expected_version=1, major_raw="Computer science"),
        )
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(_attempt_profile_mutation, attempt, outcomes)
            task_group.start_soon(_attempt_profile_mutation, attempt, outcomes)
        owner_goal = await store.mutate_goal(
            GoalMutation(
                user_id=owner_id,
                expected_profile_version=2,
                goal_id=None,
                goal_mode="TARGETED",
                occupation_id="BACKEND_DEVELOPER",
                target_by=reference_at,
                timezone="UTC",
                original_time_phrase="September 22, 2026",
                status="DRAFT",
            )
        )
        cross_user_goal_change_rejected = False
        try:
            await store.mutate_goal(
                GoalMutation(
                    user_id=other_user_id,
                    expected_profile_version=1,
                    goal_id=owner_goal.goal_id,
                    goal_mode="TARGETED",
                    occupation_id="BACKEND_DEVELOPER",
                    target_by=reference_at,
                    timezone="UTC",
                    original_time_phrase="September 22, 2026",
                    status="DRAFT",
                )
            )
        except MissingRecordError:
            cross_user_goal_change_rejected = True
        other_user_profile = await store.mutate_profile(
            _profile_mutation(other_user_id, expected_version=1, major_raw="Mathematics")
        )
        return StoreAcceptanceResult(
            conflict_count=sum(outcome.conflict is not None for outcome in outcomes),
            cross_user_goal_change_rejected=cross_user_goal_change_rejected,
            other_user_profile_version=other_user_profile.version,
            successful_profile_versions=tuple(
                sorted(
                    outcome.profile.version for outcome in outcomes if outcome.profile is not None
                )
            ),
        )
    finally:
        await database.dispose()


async def _exercise_idempotency_and_recompute_lease(database_url: str) -> None:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database)
    user_id = uuid4()
    request = IdempotencyAcquire(
        user_id=user_id,
        method="POST",
        path="/api/v1/me/goals",
        key="request-key",
        request_hash="request-hash",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    try:
        await store.create_user(UserCreate(user_id=user_id))
        acquired = await store.acquire_idempotency(request)
        assert acquired.response_status is None
        await store.complete_idempotency(
            IdempotencyComplete(request=request, response_status=201, response={"created": True})
        )
        replay = await store.acquire_idempotency(request)
        assert replay.response_status == 201
        assert replay.response == {"created": True}
        with pytest.raises(IdempotencyConflictError):
            _ = await store.acquire_idempotency(
                IdempotencyAcquire(
                    user_id=user_id,
                    method=request.method,
                    path=request.path,
                    key=request.key,
                    request_hash="different-request-hash",
                    expires_at=request.expires_at,
                )
            )
        await store.mutate_profile(_profile_mutation(user_id, expected_version=1, major_raw="Math"))
        trigger_event_id = uuid4()
        async with database.sessions.begin() as session:
            await session.execute(
                insert(user_state_events).values(
                    id=trigger_event_id,
                    user_id=user_id,
                    aggregate_id=uuid4(),
                    aggregate_type="ANALYSIS",
                    version=2,
                    kind="ANALYSIS_REQUESTED",
                    payload={"source": "acceptance"},
                )
            )
        recompute_request = await store.create_recompute_request(
            RecomputeRequestCreate(
                user_id=user_id,
                profile_version=2,
                trigger_event_id=trigger_event_id,
                dedupe_key=f"recompute:{trigger_event_id}",
                payload={"source": "acceptance"},
                context={"source": "acceptance"},
            )
        )
        assert recompute_request.state == "PENDING"
        work_items = await store.claim_recompute_work(limit=1)
        assert len(work_items) == 1
        work_item = work_items[0]
        assert work_item.request_id == recompute_request.request_id
        with pytest.raises(PersistenceConflictError):
            await store.fail_recompute(
                work_item.__class__(
                    request_id=work_item.request_id,
                    user_id=work_item.user_id,
                    profile_version=work_item.profile_version,
                    lease=work_item.lease.__class__(
                        job_id=work_item.lease.job_id,
                        lease_token=uuid4(),
                        kind=work_item.lease.kind,
                        payload=work_item.lease.payload,
                    ),
                ),
                "SOLVER_TIMEOUT",
            )
        await store.fail_recompute(work_item, "SOLVER_TIMEOUT")
    finally:
        await database.dispose()


def test_application_migrations_create_postgres_schema_when_database_is_fresh(
    acceptance_database_url: str,
) -> None:
    # Given
    database_url = acceptance_database_url

    # When
    schema = anyio.run(_schema_state, database_url)

    # Then
    assert schema.revision == HEAD_REVISION
    assert REQUIRED_TABLES <= schema.tables


def test_application_migrations_rebuild_postgres_schema_after_downgrade(
    acceptance_database_url: str,
) -> None:
    # Given
    database_url = acceptance_database_url

    # When
    _downgrade_database(database_url)
    _upgrade_database(database_url)
    schema = anyio.run(_schema_state, database_url)

    # Then
    assert schema.revision == HEAD_REVISION
    assert REQUIRED_TABLES <= schema.tables


def test_store_prevents_stale_profile_write_and_cross_user_goal_change(
    acceptance_database_url: str,
) -> None:
    # Given
    database_url = acceptance_database_url

    # When
    result = anyio.run(_exercise_store, database_url)

    # Then
    assert result.successful_profile_versions == (2,)
    assert result.conflict_count == 1
    assert result.cross_user_goal_change_rejected
    assert result.other_user_profile_version == 2


def test_store_replays_idempotency_and_fences_context_bound_recompute_lease(
    acceptance_database_url: str,
) -> None:
    # Given
    database_url = acceptance_database_url

    # When / Then
    anyio.run(_exercise_idempotency_and_recompute_lease, database_url)

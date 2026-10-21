from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import anyio
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, insert, select, update

from jobtology_be.api.analyses import require_analysis_service, router
from jobtology_be.api.idempotency import require_idempotency_store
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.application.services.analyses import (
    AnalysisRequestCommand,
    PersistentAnalysisService,
)
from jobtology_be.application.services.analysis_context import (
    ContextSnapshotConfiguration,
    SnapshotBackedAnalysisContextFactory,
)
from jobtology_be.application.services.analysis_inputs import PostgresAnalysisContextInputSource
from jobtology_be.corpus.local_snapshot import LocalJsonPublishedCorpusSnapshotReader
from jobtology_be.infrastructure.persistence.contracts import PersistenceConflictError, UserCreate
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    account_rate_limits,
    goals,
    google_identities,
    idempotency_records,
    outbox_jobs,
    profiles,
    recompute_contexts,
    recompute_requests,
)
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore

pytest_plugins = ("test_acceptance_m5_lifecycle",)
REFERENCE_AT = datetime(2026, 9, 22, 12, tzinfo=UTC)


async def _seed(database):
    owner, goal = uuid4(), uuid4()
    await PostgresApplicationStore(database).create_user(UserCreate(user_id=owner))
    async with database.sessions.begin() as session:
        await session.execute(insert(google_identities).values(
            user_id=owner, issuer="https://accounts.google.com", subject=str(owner),
        ))
        await session.execute(insert(goals).values(
            id=goal, user_id=owner, goal_mode="TARGETED", occupation_id="BACKEND_DEVELOPER",
            target_by=REFERENCE_AT + timedelta(days=30), timezone="UTC",
            original_time_phrase="next month", status="ACTIVE",
        ))
    return owner, goal


def _reader():
    return LocalJsonPublishedCorpusSnapshotReader.from_path(
        Path(__file__).parent / "fixtures/reviewed-goal-catalog.json",
    )


def _factory(database, reader):
    return SnapshotBackedAnalysisContextFactory(
        source=PostgresAnalysisContextInputSource(database), snapshot_reader=reader,
        configuration=ContextSnapshotConfiguration(
            basis_version="reviewed-v1", release_id="release-reviewed-v1",
        ),
        now=lambda: REFERENCE_AT,
    )


def test_google_account_analysis_with_idempotency_queues_and_replays_without_self_blocking(
    acceptance_database_url,
):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            owner, goal = await _seed(database)
            store = PostgresApplicationStore(database, google_accounts_only=True)
            service = PersistentAnalysisService(_factory(database, _reader()), store)
            app = FastAPI()
            app.include_router(router, prefix="/api/v1")
            app.dependency_overrides[require_authenticated_principal] = lambda: (
                AuthenticatedPrincipal(user_id=owner)
            )
            app.dependency_overrides[require_analysis_service] = lambda: service
            app.dependency_overrides[require_idempotency_store] = lambda: store
            payload = {"goal_id": str(goal), "expected_profile_version": 1, "basis_type": "EDITORIAL"}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
            ) as client:
                with anyio.fail_after(3):
                    first = await client.post(
                        "/api/v1/analyses", json=payload, headers={"Idempotency-Key": "first"},
                    )
                    replay = await client.post(
                        "/api/v1/analyses", json=payload, headers={"Idempotency-Key": "first"},
                    )
                    reuse = await client.post(
                        "/api/v1/analyses", json=payload, headers={"Idempotency-Key": "second"},
                    )
            assert first.status_code == replay.status_code == reuse.status_code == 202
            assert first.json() == replay.json() == reuse.json()
            assert first.json()["state"] == "PENDING"
            async with database.sessions() as session:
                for table in (recompute_requests, recompute_contexts, outbox_jobs):
                    assert await session.scalar(select(func.count()).select_from(table)) == 1
                assert await session.scalar(select(func.count()).select_from(idempotency_records)) == 2
                assert await session.scalar(select(account_rate_limits.c.request_count).where(
                    account_rate_limits.c.user_id == owner,
                    account_rate_limits.c.scope == "analysis_request_hour",
                )) == 1
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_standalone_analysis_rejects_profile_changes_after_reading_inputs(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            owner, goal = await _seed(database)
            reader = _reader()

            class ChangedProfileReader:
                async def get_snapshot(self, selection):
                    # A concurrent edit commits after the read-only inputs were
                    # loaded, before final submission checks the profile version.
                    async with database.sessions.begin() as session:
                        await session.execute(update(profiles).where(
                            profiles.c.user_id == owner,
                        ).values(version=profiles.c.version + 1))
                    return await reader.get_snapshot(selection)

            service = PersistentAnalysisService(
                _factory(database, ChangedProfileReader()),
                PostgresApplicationStore(database, google_accounts_only=True),
            )
            with anyio.fail_after(3), pytest.raises(PersistenceConflictError):
                await service.request(owner, AnalysisRequestCommand(
                    goal_id=goal, expected_profile_version=1, basis_type="EDITORIAL",
                ))
            async with database.sessions() as session:
                assert await session.scalar(select(profiles.c.version).where(
                    profiles.c.user_id == owner,
                )) == 2
                for table in (recompute_requests, outbox_jobs, account_rate_limits):
                    assert await session.scalar(select(func.count()).select_from(table)) == 0
        finally:
            await database.dispose()
    anyio.run(exercise)

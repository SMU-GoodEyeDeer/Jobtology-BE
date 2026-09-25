import json
from dataclasses import dataclass
from os import environ
from pathlib import Path
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    analyses,
    outbox_jobs,
    recompute_contexts,
    recompute_requests,
    route_proposals,
)
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

pytest_plugins = ("test_acceptance_m5_lifecycle",)

_LIVE_ACCEPTANCE_ENV = "JOBTOLOGY_NEO4J_LIVE_ACCEPTANCE"


@dataclass(frozen=True, slots=True)
class _TrustedIdentityProvider:
    user_id: UUID

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


@dataclass(frozen=True, slots=True)
class _PersistenceCounts:
    recompute_requests: int
    recompute_contexts: int
    outbox_jobs: int
    analyses: int
    route_proposals: int


def _write_legacy_snapshot(path: Path) -> None:
    _ = path.write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "occupation_id": "BACKEND_DEVELOPER",
                        "basis_version": "reviewed-v1",
                        "release": {
                            "release_id": "release-reviewed-v1",
                            "reviewed_at": "2026-09-25T00:00:00+00:00",
                            "state": "PUBLISHED",
                        },
                        "is_fixture": False,
                        "capability_entries": [],
                        "allowed_experience_codes": [],
                        "requirements": [],
                        "templates": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _native_settings(database_url: str, snapshot_path: Path) -> Settings:
    configured = Settings()
    if configured.db_link is None or configured.db_password is None:
        pytest.skip("typed runtime settings do not contain Neo4j credentials")
    return configured.model_copy(
        update={
            "auth_enabled": False,
            "corpus_snapshot_path": snapshot_path,
            "corpus_source": "neo4j_query_api",
            "database_url": database_url,
        }
    )


async def _persistence_counts(database_url: str) -> _PersistenceCounts:
    database = Database.create(database_url)
    try:
        async with database.sessions() as session:
            recompute_request_count = await session.scalar(select(func.count()).select_from(recompute_requests))
            recompute_context_count = await session.scalar(select(func.count()).select_from(recompute_contexts))
            outbox_job_count = await session.scalar(select(func.count()).select_from(outbox_jobs))
            analysis_count = await session.scalar(select(func.count()).select_from(analyses))
            route_proposal_count = await session.scalar(select(func.count()).select_from(route_proposals))
        return _PersistenceCounts(
            recompute_requests=recompute_request_count or 0,
            recompute_contexts=recompute_context_count or 0,
            outbox_jobs=outbox_job_count or 0,
            analyses=analysis_count or 0,
            route_proposals=route_proposal_count or 0,
        )
    finally:
        await database.dispose()


@pytest.mark.skipif(
    environ.get(_LIVE_ACCEPTANCE_ENV) != "1",
    reason=f"set {_LIVE_ACCEPTANCE_ENV}=1 for the authorized native API check",
)
def test_native_analysis_request_fails_closed_without_enqueueing_work(
    acceptance_database_url: str, tmp_path: Path
) -> None:
    # Given: a configured native default has both a real database and a legacy local snapshot.
    snapshot_path = tmp_path / "legacy-snapshot.json"
    _write_legacy_snapshot(snapshot_path)
    user_id = uuid4()
    app: FastAPI = create_app(
        _native_settings(acceptance_database_url, snapshot_path),
        dependencies=ApiDependencies(identity_provider=_TrustedIdentityProvider(user_id=user_id)),
    )
    with TestClient(app) as client:
        profile_response = client.put(
            "/api/v1/me/profile",
            json={
                "expected_profile_version": 1,
                "major_raw": "Computer Science",
                "major_concept_id": "computer-science",
                "year": 3,
                "enrollment_status": "ENROLLED",
                "expected_graduation_on": "2027-02-01",
            },
        )
        goal_response = client.post(
            "/api/v1/me/goals",
            json={
                "expected_profile_version": 2,
                "goal_mode": "TARGETED",
                "occupation_id": "BACKEND_DEVELOPER",
                "target_by": "2026-12-31T00:00:00+00:00",
                "timezone": "UTC",
                "original_time_phrase": "by the end of 2026",
            },
        )
        assert profile_response.status_code == 200
        assert goal_response.status_code == 201
        before = anyio.run(_persistence_counts, acceptance_database_url)

        # When: a valid authenticated user requests editorial analysis under the native default.
        analysis_response = client.post(
            "/api/v1/analyses",
            json={
                "goal_id": goal_response.json()["goal_id"],
                "expected_profile_version": 3,
                "basis_type": "EDITORIAL",
            },
        )
        after = anyio.run(_persistence_counts, acceptance_database_url)

    # Then: the unavailable native capability returns no producer handle and no new artifacts.
    assert analysis_response.status_code == 503
    assert set(analysis_response.json()) == {"error"}
    assert before == after

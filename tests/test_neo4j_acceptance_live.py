import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import environ
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.corpus.neo4j_client_config import Neo4jQueryApiConfig
from jobtology_be.corpus.snapshot import PublishedSnapshotSelection
from jobtology_be.corpus.source_factory import build_configured_corpus_source
from jobtology_be.infrastructure.persistence.contracts import (
    OutboxLease,
    RecomputeArtifacts,
    RecomputeFinalization,
    RecomputeWorkItem,
)
from jobtology_be.main import create_app
from jobtology_be.modules.analyses.editorial_models import InputCompleteness
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.settings import CorpusSource, Settings
from jobtology_be.workers.context import RecomputeContext
from jobtology_be.workers.recompute import (
    LeasedRecomputeWorker,
    RecomputeFailureCode,
    RecomputeWorker,
)

_LIVE_ACCEPTANCE_ENV = "JOBTOLOGY_NEO4J_LIVE_ACCEPTANCE"
_EXPECTED_NEO4J_HOST = "neo4j-1.yeongmin.net"
_TRUSTED_TEST_USER_ID = UUID("a6a1e978-bc20-458a-82c6-568f7efc69f8")


@dataclass(frozen=True, slots=True)
class _TrustedIdentityProvider:
    user_id: UUID

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


@dataclass(frozen=True, slots=True)
class _StaticContextReader:
    context: RecomputeContext

    async def get_context(self, work_item: RecomputeWorkItem) -> RecomputeContext:
        _ = work_item
        return self.context


@dataclass(frozen=True, slots=True)
class _StaticWorkClaimer:
    work_item: RecomputeWorkItem

    async def claim_recompute_work(
        self,
        limit: int,
        *,
        eligible_corpus_sources: frozenset[CorpusSource] | None = None,
    ) -> tuple[RecomputeWorkItem, ...]:
        _ = limit, eligible_corpus_sources
        return (self.work_item,)


@dataclass(frozen=True, slots=True)
class _UnreachableFinalizer:
    async def finalize_recompute(
        self, work_item: RecomputeWorkItem, artifacts: RecomputeArtifacts
    ) -> RecomputeFinalization:
        _ = work_item, artifacts
        raise AssertionError("Native source work must fail before finalization")


@dataclass(slots=True)
class _FailureRecorder:
    failure_codes: list[str]

    async def fail_recompute(self, work_item: RecomputeWorkItem, error_code: str) -> None:
        _ = work_item
        self.failure_codes.append(error_code)


def _native_settings(snapshot_path: Path | None = None) -> Settings:
    if environ.get(_LIVE_ACCEPTANCE_ENV) != "1":
        pytest.skip(f"set {_LIVE_ACCEPTANCE_ENV}=1 for the authorized read-only Neo4j check")
    configured = Settings()
    if configured.db_link is None or configured.db_password is None:
        pytest.skip("typed runtime settings do not contain Neo4j credentials")
    settings = configured.model_copy(
        update={
            "auth_enabled": False,
            "corpus_snapshot_path": snapshot_path,
            "corpus_source": "neo4j_query_api",
            "database_url": None,
        }
    )
    endpoint = urlsplit(Neo4jQueryApiConfig.from_configured_source(settings).endpoint)
    assert endpoint.scheme == "https"
    assert endpoint.hostname == _EXPECTED_NEO4J_HOST
    return settings


def _write_local_snapshot(path: Path) -> PublishedSnapshotSelection:
    selection = PublishedSnapshotSelection(
        occupation_id="LEGACY_LOCAL_OCCUPATION",
        basis_version="legacy-local-v1",
        release_id="legacy-local-release-v1",
    )
    _ = path.write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "occupation_id": selection.occupation_id,
                        "basis_version": selection.basis_version,
                        "release": {
                            "release_id": selection.release_id,
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
    return selection


def _work_item(context: RecomputeContext) -> RecomputeWorkItem:
    return RecomputeWorkItem(
        request_id=uuid4(),
        user_id=context.user_id,
        profile_version=context.profile_version,
        lease=OutboxLease(
            job_id=uuid4(),
            lease_token=uuid4(),
            kind="RECOMPUTE",
            payload={},
        ),
    )


def _native_context() -> RecomputeContext:
    reference_at = datetime.now(UTC)
    return RecomputeContext(
        user_id=_TRUSTED_TEST_USER_ID,
        profile_version=1,
        goal_id=uuid4(),
        snapshot_selection=PublishedSnapshotSelection(
            occupation_id="NATIVE_UNSUPPORTED_OCCUPATION",
            basis_version="native-v1",
            release_id="native-release-v1",
        ),
        capabilities=(),
        completeness=InputCompleteness(entities_complete=True),
        constraints=PlanningConstraints(
            target_by=reference_at + timedelta(days=1),
            available_hours_per_week=1,
        ),
        reference_at=reference_at,
        planning_started_at=reference_at,
        calendar=(),
        corpus_source="neo4j_query_api",
    )


def test_live_native_catalog_api_is_bounded_private_and_fails_closed() -> None:
    # Given
    settings = _native_settings()

    # When / Then: the default product identity remains unauthenticated.
    with TestClient(create_app(settings)) as client:
        unauthenticated_response = client.get("/api/v2/occupations", params={"limit": 1, "offset": 0})
    assert unauthenticated_response.status_code == 401
    assert set(unauthenticated_response.json()) == {"error"}

    app = create_app(
        settings,
        dependencies=ApiDependencies(identity_provider=_TrustedIdentityProvider(_TRUSTED_TEST_USER_ID)),
    )
    with TestClient(app) as client:
        occupations = client.get("/api/v2/occupations", params={"limit": 2, "offset": 0})
        occupations_next_page = client.get(
            "/api/v2/occupations", params={"limit": 1, "offset": 1}
        )
        publications = client.get("/api/v2/publications", params={"limit": 1, "offset": 0})

        assert occupations.status_code == 200
        assert occupations_next_page.status_code == 200
        assert publications.status_code == 200
        assert 1 <= len(occupations.json()) <= 2
        assert len(occupations_next_page.json()) <= 1
        pagination_respects_offset = (
            len(occupations.json()) == 2
            and len(occupations_next_page.json()) == 1
            and occupations_next_page.json()[0]["id"] == occupations.json()[1]["id"]
        )
        assert pagination_respects_offset
        assert len(publications.json()) == 1
        assert all(
            set(occupation) == {"id", "code", "kind", "name"}
            for occupation in occupations.json()
        )
        assert all(
            set(publication) == {"publication_id", "source_state", "capabilities"}
            for publication in publications.json()
        )
        assert all(
            set(publication["capabilities"])
            == {"source", "catalog", "editorial_analysis", "route_planning"}
            for publication in publications.json()
        )

        publication_id = publications.json()[0]["publication_id"]
        alignments = client.get(
            f"/api/v2/publications/{publication_id}/alignments",
            params={"limit": 1, "offset": 0},
        )
        unsupported_analysis = client.post(
            "/api/v1/analyses",
            json={
                "goal_id": str(uuid4()),
                "expected_profile_version": 1,
                "basis_type": "EDITORIAL",
            },
        )

    assert alignments.status_code == 200
    assert unsupported_analysis.status_code == 503
    assert set(unsupported_analysis.json()) == {"error"}
    assert len(alignments.json()) <= 1
    assert all(
        set(alignment)
        == {
            "publication_id",
            "source_enrichment_id",
            "source_posting_id",
            "source_current",
            "accepted",
            "competency",
        }
        and set(alignment["competency"]) == {"id", "code", "kind", "name"}
        for alignment in alignments.json()
    )


@pytest.mark.anyio
async def test_native_worker_fails_closed_while_legacy_local_context_remains_readable(
    tmp_path: Path,
) -> None:
    # Given
    selection = _write_local_snapshot(tmp_path / "legacy-local-snapshot.json")
    source = build_configured_corpus_source(_native_settings(tmp_path / "legacy-local-snapshot.json"))
    context = _native_context()
    work_item = _work_item(context)
    failure_recorder = _FailureRecorder(failure_codes=[])
    worker = LeasedRecomputeWorker(
        claimer=_StaticWorkClaimer(work_item),
        processor=RecomputeWorker(
            context_reader=_StaticContextReader(context),
            snapshot_reader=source.legacy_snapshot_reader,
            source_snapshot_reader=source,
            finalizer=_UnreachableFinalizer(),
        ),
        failure_finalizer=failure_recorder,
    )

    # When
    try:
        finalizations = await worker.process_once(limit=1)
        legacy_snapshot = await source.get_snapshot(source="local_json", selection=selection)
    finally:
        await source.aclose()

    # Then
    assert finalizations == ()
    assert failure_recorder.failure_codes == [RecomputeFailureCode.NATIVE_SOURCE_UNSUPPORTED.value]
    assert legacy_snapshot.require_selection(selection) is legacy_snapshot

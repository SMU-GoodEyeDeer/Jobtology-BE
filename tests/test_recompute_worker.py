from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from jobtology_be.contracts import CapabilityInput
from jobtology_be.corpus.snapshot import (
    FixtureSnapshotForbiddenError,
    PublishedCorpusSnapshot,
    PublishedSnapshotSelection,
    PublishedSnapshotUnavailableError,
)
from jobtology_be.infrastructure.persistence.contracts import (
    JsonValue,
    OutboxLease,
    PersistenceConflictError,
    RecomputeArtifacts,
    RecomputeFinalization,
    RecomputeWorkItem,
)
from jobtology_be.modules.analyses.editorial_models import (
    EditorialReleaseMetadata,
    EditorialRequirement,
    InputCompleteness,
    ReleaseState,
    RequirementNecessity,
)
from jobtology_be.modules.profiles.normalizer import CapabilityCatalogEntry
from jobtology_be.planning.candidate_models import ActivityTemplate, KnownKrwCost
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.planning.solver_models import PlanningSlot, SolverSettings
from jobtology_be.settings import CorpusSource
from jobtology_be.workers.context import JsonRecomputeContextReader, RecomputeContextDocument
from jobtology_be.workers.recompute import (
    LeasedRecomputeWorker,
    PublishedSnapshotIdentityMismatchError,
    RecomputeContext,
    RecomputeFailureCode,
    RecomputeWorker,
    StaleProfileError,
    build_leased_recompute_worker,
)

REFERENCE_TIME = datetime(2026, 9, 22, 12, tzinfo=UTC)
USER_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
GOAL_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b5")
REQUEST_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b6")
JOB_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b7")
LEASE_TOKEN = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b8")


class StaticContextReader:
    def __init__(self, context: RecomputeContext) -> None:
        self.context = context

    async def get_context(self, work_item: RecomputeWorkItem) -> RecomputeContext:
        _ = work_item
        return self.context


class StaticSnapshotReader:
    def __init__(self, snapshot: PublishedCorpusSnapshot) -> None:
        self.snapshot = snapshot
        self.selections: list[PublishedSnapshotSelection] = []

    async def get_snapshot(self, selection: PublishedSnapshotSelection) -> PublishedCorpusSnapshot:
        self.selections.append(selection)
        return self.snapshot


class SourceAwareSnapshotReader:
    def __init__(self, snapshot: PublishedCorpusSnapshot) -> None:
        self.snapshot = snapshot
        self.sources: list[str] = []

    async def get_snapshot(
        self,
        *,
        source: str,
        selection: PublishedSnapshotSelection,
    ) -> PublishedCorpusSnapshot:
        self.sources.append(source)
        if source == "neo4j_query_api":
            raise PublishedSnapshotUnavailableError(selection=selection)
        return self.snapshot


class RefreshingSnapshotReader:
    def __init__(self, snapshot: PublishedCorpusSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def get_snapshot(self, selection: PublishedSnapshotSelection) -> PublishedCorpusSnapshot:
        self.calls += 1
        if self.calls == 1:
            return self.snapshot
        raise PublishedSnapshotUnavailableError(selection=selection)


class RecordingFinalizer:
    def __init__(self) -> None:
        self.work_items: list[RecomputeWorkItem] = []
        self.artifacts: list[RecomputeArtifacts] = []

    async def finalize_recompute(
        self, work_item: RecomputeWorkItem, artifacts: RecomputeArtifacts
    ) -> RecomputeFinalization:
        self.work_items.append(work_item)
        self.artifacts.append(artifacts)
        return RecomputeFinalization(
            request_id=work_item.request_id,
            analysis_id=artifacts.analysis.analysis_id,
            proposal_id=None if artifacts.proposal is None else artifacts.proposal.proposal_id,
            updated_latest_analysis=True,
        )


class StaticWorkClaimer:
    def __init__(self, work_items: tuple[RecomputeWorkItem, ...]) -> None:
        self.work_items = work_items
        self.limits: list[int] = []
        self.eligible_corpus_sources: list[frozenset[CorpusSource] | None] = []

    async def claim_recompute_work(
        self,
        limit: int,
        *,
        eligible_corpus_sources: frozenset[CorpusSource] | None = None,
    ) -> tuple[RecomputeWorkItem, ...]:
        self.limits.append(limit)
        self.eligible_corpus_sources.append(eligible_corpus_sources)
        return self.work_items


class LostLeaseFinalizer:
    def __init__(self) -> None:
        self.artifacts: list[RecomputeArtifacts] = []

    async def finalize_recompute(
        self, work_item: RecomputeWorkItem, artifacts: RecomputeArtifacts
    ) -> RecomputeFinalization:
        _ = work_item
        self.artifacts.append(artifacts)
        raise PersistenceConflictError(resource="outbox lease")


class RecordingFailureFinalizer:
    def __init__(self) -> None:
        self.failed: list[tuple[RecomputeWorkItem, str]] = []

    async def fail_recompute(
        self, work_item: RecomputeWorkItem, error_code: str
    ) -> None:
        self.failed.append((work_item, error_code))


class StaticContextPayloadReader:
    def __init__(self, payload: dict[str, JsonValue]) -> None:
        self.payload = payload
        self.work_items: list[RecomputeWorkItem] = []

    async def load_recompute_context(self, work_item: RecomputeWorkItem) -> dict[str, JsonValue]:
        self.work_items.append(work_item)
        return self.payload


class ExecutableWorkerStore:
    def __init__(self, payload: dict[str, JsonValue], work_items: tuple[RecomputeWorkItem, ...]) -> None:
        self.payload = payload
        self.work_items = work_items
        self.context_work_items: list[RecomputeWorkItem] = []
        self.artifacts: list[RecomputeArtifacts] = []
        self.failure_codes: list[str] = []
        self.eligible_corpus_sources: list[frozenset[CorpusSource] | None] = []

    async def claim_recompute_work(
        self,
        limit: int,
        *,
        eligible_corpus_sources: frozenset[CorpusSource] | None = None,
    ) -> tuple[RecomputeWorkItem, ...]:
        self.eligible_corpus_sources.append(eligible_corpus_sources)
        return self.work_items[:limit]

    async def load_recompute_context(self, work_item: RecomputeWorkItem) -> dict[str, JsonValue]:
        self.context_work_items.append(work_item)
        return self.payload

    async def finalize_recompute(
        self, work_item: RecomputeWorkItem, artifacts: RecomputeArtifacts
    ) -> RecomputeFinalization:
        self.artifacts.append(artifacts)
        return RecomputeFinalization(
            request_id=work_item.request_id,
            analysis_id=artifacts.analysis.analysis_id,
            proposal_id=None if artifacts.proposal is None else artifacts.proposal.proposal_id,
            updated_latest_analysis=True,
        )

    async def fail_recompute(self, work_item: RecomputeWorkItem, error_code: str) -> None:
        _ = work_item
        self.failure_codes.append(error_code)


def _selection() -> PublishedSnapshotSelection:
    return PublishedSnapshotSelection(
        occupation_id="BACKEND_DEVELOPER",
        basis_version="reviewed-v1",
        release_id="release-reviewed-v1",
    )


def _snapshot(
    *,
    occupation_id: str = "BACKEND_DEVELOPER",
    release_state: ReleaseState = ReleaseState.PUBLISHED,
    is_fixture: bool = False,
) -> PublishedCorpusSnapshot:
    return PublishedCorpusSnapshot(
        occupation_id=occupation_id,
        basis_version="reviewed-v1",
        release=EditorialReleaseMetadata(
            release_id="release-reviewed-v1",
            state=release_state,
            reviewed_at=REFERENCE_TIME,
        ),
        is_fixture=is_fixture,
        capability_entries=(
            CapabilityCatalogEntry(
                entity_id="capability-api",
                aliases=frozenset({"API implementation"}),
            ),
        ),
        allowed_experience_codes=frozenset({"DELIVERED"}),
        requirements=(
            EditorialRequirement(
                requirement_key="api",
                label="API implementation",
                necessity=RequirementNecessity.REQUIRED,
                entity_id="capability-api",
                required_experience_codes=frozenset({"DELIVERED"}),
                support_refs=frozenset({"review:api"}),
            ),
        ),
        templates=(
            ActivityTemplate(
                action_id="api-project",
                revision=4,
                title="API project",
                estimated_hours=1,
                outcome_requirement_keys=frozenset({"api"}),
                prerequisite_action_ids=(),
                completion_criteria=("publish API",),
                support_refs=frozenset({"template:api-project"}),
                cost=KnownKrwCost(krw=0),
                is_foundational=False,
            ),
        ),
    )


def _context(
    *,
    profile_version: int = 3,
    solver_settings: SolverSettings | None = None,
    capabilities: tuple[CapabilityInput, ...] = (CapabilityInput(raw_text="API implementation"),),
) -> RecomputeContext:
    return RecomputeContext(
        user_id=USER_ID,
        profile_version=profile_version,
        goal_id=GOAL_ID,
        snapshot_selection=_selection(),
        capabilities=capabilities,
        completeness=InputCompleteness(
            entities_complete=True,
            experience_complete_entity_ids=frozenset({"capability-api"}),
        ),
        constraints=PlanningConstraints(
            target_by=REFERENCE_TIME + timedelta(days=1),
            available_hours_per_week=1,
        ),
        reference_at=REFERENCE_TIME,
        planning_started_at=REFERENCE_TIME,
        calendar=(
            PlanningSlot(
                starts_at=REFERENCE_TIME,
                ends_at=REFERENCE_TIME + timedelta(hours=1),
                capacity_week_key="2026-W39",
            ),
        ),
        solver_settings=SolverSettings() if solver_settings is None else solver_settings,
    )


def _work_item() -> RecomputeWorkItem:
    return RecomputeWorkItem(
        request_id=REQUEST_ID,
        user_id=USER_ID,
        profile_version=3,
        lease=OutboxLease(
            job_id=JOB_ID,
            lease_token=LEASE_TOKEN,
            kind="RECOMPUTE",
            payload={"recompute_request_id": str(REQUEST_ID)},
        ),
    )


def _context_payload() -> dict[str, JsonValue]:
    return {
        "user_id": str(USER_ID),
        "profile_version": 3,
        "goal_id": str(GOAL_ID),
        "snapshot_selection": {
            "occupation_id": "BACKEND_DEVELOPER",
            "basis_version": "reviewed-v1",
            "release_id": "release-reviewed-v1",
        },
        "capabilities": [{"raw_text": "API implementation", "experience_codes": []}],
        "completeness": {
            "entities_complete": True,
            "experience_complete_entity_ids": ["capability-api"],
        },
        "constraints": {
            "target_by": (REFERENCE_TIME + timedelta(days=1)).isoformat(),
            "available_hours_per_week": 1,
        },
        "reference_at": REFERENCE_TIME.isoformat(),
        "planning_started_at": REFERENCE_TIME.isoformat(),
        "calendar": [
            {
                "starts_at": REFERENCE_TIME.isoformat(),
                "ends_at": (REFERENCE_TIME + timedelta(hours=1)).isoformat(),
                "capacity_week_key": "2026-W39",
            }
        ],
        "candidate_availability": [],
        "solver_settings": {"time_limit_seconds": 20.0},
    }


@pytest.mark.anyio
async def test_recompute_worker_finalizes_pinned_analysis_and_route_atomically() -> None:
    # Given
    snapshot_reader = StaticSnapshotReader(_snapshot())
    finalizer = RecordingFinalizer()
    worker = RecomputeWorker(StaticContextReader(_context()), snapshot_reader, finalizer)

    # When
    result = await worker.process(_work_item())

    # Then
    assert result.updated_latest_analysis
    assert snapshot_reader.selections == [_selection()]
    assert finalizer.work_items == [_work_item()]
    assert len(finalizer.artifacts) == 1
    artifacts = finalizer.artifacts[0]
    assert artifacts.analysis.release_id == "release-reviewed-v1"
    assert artifacts.analysis.results == {
        "analysis_id": str(artifacts.analysis.analysis_id),
        "analysis_status": "READY",
        "basis_version": "reviewed-v1",
        "corpus_release_id": "release-reviewed-v1",
        "is_fixture": False,
        "occupation_id": "BACKEND_DEVELOPER",
        "preferred_coverage": {
            "availability": "UNAVAILABLE",
            "matched_count": 0,
            "reason": "NO_REQUIREMENTS",
            "score": None,
            "score_method": None,
            "total_count": 0,
        },
        "profile_version": 3,
        "reference_at": "2026-09-22T12:00:00+00:00",
        "required_coverage": {
            "availability": "AVAILABLE",
            "matched_count": 0,
            "reason": None,
            "score": 0.0,
            "score_method": "REVIEWED_CHECKLIST",
            "total_count": 1,
        },
        "requirements": (
            {
                "label": "API implementation",
                "necessity": "REQUIRED",
                "provenance": ("SELF_REPORTED",),
                "requirement_key": "api",
                "status": "UNMET",
                "support_refs": ("review:api",),
            },
        ),
    }
    assert artifacts.proposal is not None
    assert artifacts.proposal.steps[0]["outcome_requirement_keys"] == ("api",)
    assert artifacts.proposal.steps[0]["outcomes"] == (
        {
            "requirement_key": "api",
            "entity_id": "capability-api",
            "raw_text": "API implementation",
            "experience_codes": ("DELIVERED",),
        },
    )
    assert artifacts.proposal.steps[0]["support_refs"] == ("template:api-project",)
    assert artifacts.proposal.trace_versions["template_versions"] == {
        "api-project": 4
    }


@pytest.mark.anyio
async def test_recompute_worker_rejects_stale_context_before_snapshot_or_finalization() -> None:
    # Given
    snapshot_reader = StaticSnapshotReader(_snapshot())
    finalizer = RecordingFinalizer()
    worker = RecomputeWorker(StaticContextReader(_context(profile_version=4)), snapshot_reader, finalizer)

    # When / Then
    with pytest.raises(StaleProfileError):
        _ = await worker.process(_work_item())
    assert snapshot_reader.selections == []
    assert finalizer.artifacts == []


@pytest.mark.anyio
async def test_recompute_worker_rejects_mismatched_snapshot_identity_before_finalization() -> None:
    # Given
    finalizer = RecordingFinalizer()
    worker = RecomputeWorker(
        StaticContextReader(_context()), StaticSnapshotReader(_snapshot(occupation_id="FRONTEND_DEVELOPER")), finalizer
    )

    # When / Then
    with pytest.raises(PublishedSnapshotIdentityMismatchError):
        _ = await worker.process(_work_item())
    assert finalizer.artifacts == []


@pytest.mark.anyio
async def test_recompute_worker_refreshes_publication_on_every_recompute() -> None:
    # Given
    finalizer = RecordingFinalizer()
    snapshot_reader = RefreshingSnapshotReader(_snapshot())
    worker = RecomputeWorker(StaticContextReader(_context()), snapshot_reader, finalizer)

    # When
    _ = await worker.process(_work_item())

    # Then
    with pytest.raises(PublishedSnapshotUnavailableError):
        _ = await worker.process(_work_item())
    assert snapshot_reader.calls == 2
    assert len(finalizer.artifacts) == 1


@pytest.mark.anyio
async def test_recompute_worker_forbids_fixture_snapshots_without_explicit_opt_in() -> None:
    # Given
    finalizer = RecordingFinalizer()
    worker = RecomputeWorker(
        StaticContextReader(_context()),
        StaticSnapshotReader(_snapshot(release_state=ReleaseState.FIXTURE, is_fixture=True)),
        finalizer,
    )

    # When / Then
    with pytest.raises(FixtureSnapshotForbiddenError):
        _ = await worker.process(_work_item())
    assert finalizer.artifacts == []


@pytest.mark.anyio
async def test_leased_recompute_worker_processes_each_claimed_item_once() -> None:
    # Given
    finalizer = RecordingFinalizer()
    processor = RecomputeWorker(StaticContextReader(_context()), StaticSnapshotReader(_snapshot()), finalizer)
    claimer = StaticWorkClaimer((_work_item(),))
    worker = LeasedRecomputeWorker(
        claimer=claimer,
        processor=processor,
        failure_finalizer=RecordingFailureFinalizer(),
    )

    # When
    results = await worker.process_once(limit=5)

    # Then
    assert claimer.limits == [5]
    assert len(results) == 1
    assert finalizer.work_items == [_work_item()]


@pytest.mark.anyio
async def test_recompute_worker_does_not_publish_after_finalization_loses_its_lease() -> None:
    # Given
    finalizer = LostLeaseFinalizer()
    worker = RecomputeWorker(StaticContextReader(_context()), StaticSnapshotReader(_snapshot()), finalizer)

    # When / Then
    with pytest.raises(PersistenceConflictError):
        _ = await worker.process(_work_item())
    assert len(finalizer.artifacts) == 1


@pytest.mark.anyio
async def test_leased_recompute_worker_terminalizes_cp_sat_timeout_without_publishing() -> None:
    # Given
    publication = RecordingFinalizer()
    failures = RecordingFailureFinalizer()
    processor = RecomputeWorker(
        StaticContextReader(_context(solver_settings=SolverSettings(time_limit_seconds=0))),
        StaticSnapshotReader(_snapshot()),
        publication,
    )
    worker = LeasedRecomputeWorker(
        claimer=StaticWorkClaimer((_work_item(),)),
        processor=processor,
        failure_finalizer=failures,
    )

    # When
    results = await worker.process_once(limit=1)

    # Then
    assert results == ()
    assert publication.artifacts == []
    assert failures.failed == [(_work_item(), RecomputeFailureCode.SOLVER_TIMEOUT.value)]


@pytest.mark.anyio
async def test_recompute_worker_excludes_satisfied_requirements_from_cp_sat_coverage() -> None:
    # Given
    finalizer = RecordingFinalizer()
    worker = RecomputeWorker(
        StaticContextReader(
            _context(
                capabilities=(
                    CapabilityInput(
                        raw_text="API implementation",
                        experience_codes=["DELIVERED"],
                    ),
                )
            )
        ),
        StaticSnapshotReader(_snapshot()),
        finalizer,
    )

    # When
    _ = await worker.process(_work_item())

    # Then
    assert finalizer.artifacts[0].proposal is not None
    assert finalizer.artifacts[0].proposal.feasibility == "FEASIBLE"


@pytest.mark.anyio
async def test_leased_recompute_worker_terminalizes_a_stale_context_without_publishing() -> None:
    # Given
    publication = RecordingFinalizer()
    failures = RecordingFailureFinalizer()
    processor = RecomputeWorker(
        StaticContextReader(_context(profile_version=4)),
        StaticSnapshotReader(_snapshot()),
        publication,
    )
    worker = LeasedRecomputeWorker(
        claimer=StaticWorkClaimer((_work_item(),)),
        processor=processor,
        failure_finalizer=failures,
    )

    # When
    results = await worker.process_once(limit=1)

    # Then
    assert results == ()
    assert publication.artifacts == []
    assert failures.failed == [(_work_item(), RecomputeFailureCode.STALE_PROFILE.value)]


@pytest.mark.anyio
async def test_leased_recompute_worker_terminalizes_explicit_neo4j_without_local_fallback() -> None:
    # Given
    local_reader = StaticSnapshotReader(_snapshot())
    source_reader = SourceAwareSnapshotReader(_snapshot())
    publication = RecordingFinalizer()
    failures = RecordingFailureFinalizer()
    processor = RecomputeWorker(
        StaticContextReader(replace(_context(), corpus_source="neo4j_query_api")),
        local_reader,
        publication,
        source_snapshot_reader=source_reader,
    )
    claimer = StaticWorkClaimer((_work_item(),))
    worker = LeasedRecomputeWorker(
        claimer=claimer,
        processor=processor,
        failure_finalizer=failures,
        eligible_corpus_sources=frozenset({"neo4j_query_api"}),
    )

    # When
    results = await worker.process_once(limit=1)

    # Then
    assert results == ()
    assert source_reader.sources == []
    assert local_reader.selections == []
    assert publication.artifacts == []
    assert failures.failed == [(_work_item(), RecomputeFailureCode.NATIVE_SOURCE_UNSUPPORTED.value)]
    assert claimer.eligible_corpus_sources == [frozenset({"neo4j_query_api"})]


@pytest.mark.anyio
async def test_recompute_worker_replays_untagged_context_through_local_source_dispatch() -> None:
    # Given
    payload_reader = StaticContextPayloadReader(_context_payload())
    source_reader = SourceAwareSnapshotReader(_snapshot())
    local_reader = StaticSnapshotReader(_snapshot())
    finalizer = RecordingFinalizer()
    worker = RecomputeWorker(
        JsonRecomputeContextReader(payload_reader=payload_reader),
        local_reader,
        finalizer,
        source_snapshot_reader=source_reader,
    )

    # When
    _ = await worker.process(_work_item())

    # Then
    assert source_reader.sources == ["local_json"]
    assert local_reader.selections == []
    assert len(finalizer.artifacts) == 1


@pytest.mark.anyio
async def test_json_context_reader_uses_only_the_persisted_payload() -> None:
    # Given
    payload_reader = StaticContextPayloadReader(_context_payload())
    reader = JsonRecomputeContextReader(payload_reader=payload_reader)

    # When
    context = await reader.get_context(_work_item())

    # Then
    assert payload_reader.work_items == [_work_item()]
    assert context.user_id == USER_ID
    assert context.profile_version == 3
    assert context.goal_id == GOAL_ID
    assert context.snapshot_selection == _selection()
    assert context.calendar[0].ends_at - context.calendar[0].starts_at == timedelta(hours=1)


@pytest.mark.anyio
async def test_build_leased_recompute_worker_composes_store_and_persisted_context() -> None:
    # Given
    store = ExecutableWorkerStore(_context_payload(), (_work_item(),))
    worker = build_leased_recompute_worker(store=store, snapshot_reader=StaticSnapshotReader(_snapshot()))

    # When
    finalizations = await worker.process_once(limit=1)

    # Then
    assert len(finalizations) == 1
    assert store.context_work_items == [_work_item()]
    assert len(store.artifacts) == 1
    assert store.failure_codes == []
    assert store.eligible_corpus_sources == [None]


def test_recompute_context_document_forbids_untyped_extra_payload_fields() -> None:
    # Given
    payload = _context_payload() | {"live_profile_lookup": True}

    # When / Then
    with pytest.raises(ValidationError):
        _ = RecomputeContextDocument.model_validate(payload)


def test_recompute_context_document_defaults_solver_settings() -> None:
    # Given
    payload = _context_payload() | {"solver_settings": {}}

    # When
    document = RecomputeContextDocument.model_validate(payload)

    # Then
    assert document.solver_settings.time_limit_seconds == 20.0

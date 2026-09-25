from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, assert_never, override
from uuid import uuid4

import anyio

from jobtology_be.corpus.snapshot import (
    CorpusSnapshotError,
    PublishedCorpusSnapshot,
    PublishedCorpusSnapshotReader,
    PublishedSnapshotIdentityMismatchError,
    PublishedSnapshotSelection,
)
from jobtology_be.infrastructure.persistence.contracts import (
    PersistenceConflictError,
    RecomputeArtifacts,
    RecomputeFinalization,
    RecomputeWorkItem,
)
from jobtology_be.modules.analyses.editorial import EditorialGapAnalyzer
from jobtology_be.modules.analyses.editorial_models import (
    EditorialAnalysisRequest,
    EditorialBaselineError,
)
from jobtology_be.modules.profiles.normalizer import (
    CatalogProfileNormalizer,
    ConflictingEntityIdError,
    UnknownEntityIdError,
    UnknownExperienceCodeError,
)
from jobtology_be.planning.candidate_models import CandidateTemplateError
from jobtology_be.planning.candidates import CandidateGenerator
from jobtology_be.planning.cp_sat_planner import CpSatRoutePlanner
from jobtology_be.planning.solver_models import InvalidPlanningProblemError
from jobtology_be.settings import CorpusSource
from jobtology_be.workers.context import (
    InvalidRecomputeContextError,
    JsonRecomputeContextReader,
    RecomputeContext,
    RecomputeContextPayloadReader,
)
from jobtology_be.workers.outcomes import PinnedOutcomeResolutionError
from jobtology_be.workers.recompute_artifacts import build_recompute_artifacts

from .recompute_planning import build_planning_problem

__all__ = [
    "LeasedRecomputeWorker",
    "NativeSourcePlanningUnsupportedError",
    "PublishedSnapshotIdentityMismatchError",
    "RecomputeContext",
    "RecomputeFailureCode",
    "RecomputeWorker",
    "SolverTimeoutError",
    "StaleProfileError",
    "build_leased_recompute_worker",
]


class RecomputeFailureCode(StrEnum):
    STALE_PROFILE = "STALE_PROFILE"
    CORPUS_SNAPSHOT = "CORPUS_SNAPSHOT"
    NATIVE_SOURCE_UNSUPPORTED = "NATIVE_SOURCE_UNSUPPORTED"
    INVALID_INPUT = "INVALID_INPUT"
    SOLVER_TIMEOUT = "SOLVER_TIMEOUT"


@dataclass(frozen=True, slots=True)
class StaleProfileError(Exception):
    request_profile_version: int
    context_profile_version: int

    @override
    def __str__(self) -> str:
        return "recompute context does not match the leased profile version"


@dataclass(frozen=True, slots=True)
class SolverTimeoutError(Exception):
    @override
    def __str__(self) -> str:
        return "CP-SAT route planning timed out"


@dataclass(frozen=True, slots=True)
class NativeSourcePlanningUnsupportedError(Exception):
    @override
    def __str__(self) -> str:
        return "native source lacks a verified editorial baseline and planning templates"


class RecomputeContextReader(Protocol):
    async def get_context(self, work_item: RecomputeWorkItem) -> RecomputeContext: ...


class SourceAwarePublishedCorpusSnapshotReader(Protocol):
    async def get_snapshot(
        self,
        *,
        source: CorpusSource,
        selection: PublishedSnapshotSelection,
    ) -> PublishedCorpusSnapshot: ...


class RecomputeFinalizer(Protocol):
    async def finalize_recompute(
        self, work_item: RecomputeWorkItem, artifacts: RecomputeArtifacts
    ) -> RecomputeFinalization: ...


class RecomputeWorkClaimer(Protocol):
    async def claim_recompute_work(
        self,
        limit: int,
        *,
        eligible_corpus_sources: frozenset[CorpusSource] | None = None,
    ) -> tuple[RecomputeWorkItem, ...]: ...


class RecomputeFailureFinalizer(Protocol):
    async def fail_recompute(self, work_item: RecomputeWorkItem, error_code: str) -> None: ...


class RecomputeWorkerStore(
    RecomputeContextPayloadReader,
    RecomputeFailureFinalizer,
    RecomputeFinalizer,
    RecomputeWorkClaimer,
    Protocol,
):
    pass


@dataclass(frozen=True, slots=True)
class RecomputeWorker:
    context_reader: RecomputeContextReader
    snapshot_reader: PublishedCorpusSnapshotReader
    finalizer: RecomputeFinalizer
    allow_fixture: bool = False
    source_snapshot_reader: SourceAwarePublishedCorpusSnapshotReader | None = None

    async def process(self, work_item: RecomputeWorkItem) -> RecomputeFinalization:
        context = await self.context_reader.get_context(work_item)
        if (
            context.user_id != work_item.user_id
            or context.profile_version != work_item.profile_version
        ):
            raise StaleProfileError(
                request_profile_version=work_item.profile_version,
                context_profile_version=context.profile_version,
            )
        match context.corpus_source:
            case "local_json":
                pass
            case "neo4j_query_api":
                raise NativeSourcePlanningUnsupportedError()
            case unreachable:
                assert_never(unreachable)
        if self.source_snapshot_reader is None:
            snapshot = await self.snapshot_reader.get_snapshot(context.snapshot_selection)
        else:
            snapshot = await self.source_snapshot_reader.get_snapshot(
                source=context.corpus_source,
                selection=context.snapshot_selection,
            )
        snapshot.require_selection(context.snapshot_selection)
        baseline = snapshot.baseline_for_recompute(allow_fixture=self.allow_fixture)
        normalized_profile = CatalogProfileNormalizer(
            entries=snapshot.capability_entries,
            allowed_experience_codes=snapshot.allowed_experience_codes,
        ).normalize(
            user_id=str(context.user_id),
            profile_version=context.profile_version,
            capabilities=list(context.capabilities),
        )
        analysis = EditorialGapAnalyzer(baseline).evaluate(
            normalized_profile,
            EditorialAnalysisRequest(
                analysis_id=str(uuid4()),
                occupation_id=context.snapshot_selection.occupation_id,
                basis_version=context.snapshot_selection.basis_version,
                reference_at=context.reference_at,
            ),
            context.completeness,
        )
        candidate_set = CandidateGenerator(snapshot.templates).build(analysis)
        planning_result = await anyio.to_thread.run_sync(
            CpSatRoutePlanner().plan,
            build_planning_problem(context, analysis, candidate_set),
        )
        if planning_result.feasibility is None:
            raise SolverTimeoutError()
        return await self.finalizer.finalize_recompute(
            work_item,
            build_recompute_artifacts(
                context=context,
                work_item=work_item,
                analysis=analysis,
                baseline=baseline,
                templates=snapshot.templates,
                planning_result=planning_result,
            ),
        )


@dataclass(frozen=True, slots=True)
class LeasedRecomputeWorker:
    claimer: RecomputeWorkClaimer
    processor: RecomputeWorker
    failure_finalizer: RecomputeFailureFinalizer
    eligible_corpus_sources: frozenset[CorpusSource] | None = None

    async def process_once(self, *, limit: int) -> tuple[RecomputeFinalization, ...]:
        work_items = await self.claimer.claim_recompute_work(
            limit,
            eligible_corpus_sources=self.eligible_corpus_sources,
        )
        finalizations: list[RecomputeFinalization] = []
        for work_item in work_items:
            try:
                finalizations.append(await self.processor.process(work_item))
            except PersistenceConflictError:
                continue
            except StaleProfileError:
                await self.failure_finalizer.fail_recompute(
                    work_item, RecomputeFailureCode.STALE_PROFILE.value
                )
            except NativeSourcePlanningUnsupportedError:
                await self.failure_finalizer.fail_recompute(
                    work_item, RecomputeFailureCode.NATIVE_SOURCE_UNSUPPORTED.value
                )
            except CorpusSnapshotError:
                await self.failure_finalizer.fail_recompute(
                    work_item, RecomputeFailureCode.CORPUS_SNAPSHOT.value
                )
            except SolverTimeoutError:
                await self.failure_finalizer.fail_recompute(
                    work_item, RecomputeFailureCode.SOLVER_TIMEOUT.value
                )
            except (
                CandidateTemplateError,
                ConflictingEntityIdError,
                EditorialBaselineError,
                InvalidRecomputeContextError,
                InvalidPlanningProblemError,
                PinnedOutcomeResolutionError,
                UnknownEntityIdError,
                UnknownExperienceCodeError,
            ):
                await self.failure_finalizer.fail_recompute(
                    work_item, RecomputeFailureCode.INVALID_INPUT.value
                )
        return tuple(finalizations)


def build_leased_recompute_worker(
    *,
    store: RecomputeWorkerStore,
    snapshot_reader: PublishedCorpusSnapshotReader,
    allow_fixture: bool = False,
    source_snapshot_reader: SourceAwarePublishedCorpusSnapshotReader | None = None,
    eligible_corpus_sources: frozenset[CorpusSource] | None = None,
) -> LeasedRecomputeWorker:
    return LeasedRecomputeWorker(
        claimer=store,
        processor=RecomputeWorker(
            context_reader=JsonRecomputeContextReader(payload_reader=store),
            snapshot_reader=snapshot_reader,
            finalizer=store,
            allow_fixture=allow_fixture,
            source_snapshot_reader=source_snapshot_reader,
        ),
        failure_finalizer=store,
        eligible_corpus_sources=eligible_corpus_sources,
    )

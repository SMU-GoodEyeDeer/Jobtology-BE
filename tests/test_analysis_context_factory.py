from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import anyio

from jobtology_be.application.services.analyses import AnalysisRequestCommand
from jobtology_be.application.services.analysis_context import (
    AnalysisContextInputs,
    ContextSnapshotConfiguration,
    SnapshotBackedAnalysisContextFactory,
)
from jobtology_be.contracts import CapabilityInput
from jobtology_be.corpus.snapshot import PublishedCorpusSnapshot, PublishedSnapshotSelection
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
from jobtology_be.workers.context import RecomputeContextDocument

USER_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
GOAL_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b5")
REFERENCE_AT = datetime(2026, 9, 22, 12, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class StaticInputSource:
    inputs: AnalysisContextInputs
    expected_reference_at: datetime

    async def load_context_inputs(
        self,
        user_id: UUID,
        command: AnalysisRequestCommand,
        reference_at: datetime,
    ) -> AnalysisContextInputs:
        assert user_id == USER_ID
        assert command.goal_id == GOAL_ID
        assert reference_at == self.expected_reference_at
        return self.inputs


@dataclass(frozen=True, slots=True)
class StaticSnapshotReader:
    snapshot: PublishedCorpusSnapshot

    async def get_snapshot(self, selection: PublishedSnapshotSelection) -> PublishedCorpusSnapshot:
        return self.snapshot.require_selection(selection)


def _snapshot() -> PublishedCorpusSnapshot:
    return PublishedCorpusSnapshot(
        occupation_id="BACKEND_DEVELOPER",
        basis_version="reviewed-v1",
        release=EditorialReleaseMetadata(
            release_id="release-reviewed-v1",
            state=ReleaseState.PUBLISHED,
            reviewed_at=REFERENCE_AT,
        ),
        is_fixture=False,
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
                revision=1,
                title="API project",
                estimated_hours=8,
                outcome_requirement_keys=frozenset({"api"}),
                prerequisite_action_ids=(),
                completion_criteria=("publish API",),
                support_refs=frozenset({"template:api-project"}),
                cost=KnownKrwCost(krw=0),
                is_foundational=False,
            ),
        ),
    )


def _context_document(
    source: Literal["neo4j_query_api"] | None = None,
) -> RecomputeContextDocument:
    snapshot_selection = {
        "occupation_id": "BACKEND_DEVELOPER",
        "basis_version": "reviewed-v1",
        "release_id": "release-reviewed-v1",
    }
    if source is not None:
        snapshot_selection["source"] = source
    return RecomputeContextDocument.model_validate(
        {
            "user_id": USER_ID,
            "profile_version": 3,
            "goal_id": GOAL_ID,
            "snapshot_selection": snapshot_selection,
            "capabilities": (),
            "completeness": {"entities_complete": True},
            "constraints": PlanningConstraints(
                target_by=REFERENCE_AT + timedelta(days=30),
                available_hours_per_week=4,
            ),
            "reference_at": REFERENCE_AT,
            "planning_started_at": REFERENCE_AT,
            "calendar": (),
        }
    )


def test_recompute_context_defaults_untagged_selection_to_legacy_local_json() -> None:
    # Given
    document = _context_document()

    # When
    context = document.to_context()

    # Then
    assert context.corpus_source == "local_json"


def test_recompute_context_preserves_explicit_neo4j_query_api_selection() -> None:
    # Given
    document = _context_document(source="neo4j_query_api")

    # When
    context = document.to_context()

    # Then
    assert context.corpus_source == "neo4j_query_api"


def test_snapshot_backed_context_factory_pins_current_inputs_and_release() -> None:
    # Given
    inputs = AnalysisContextInputs(
        occupation_id="BACKEND_DEVELOPER",
        capabilities=(
            CapabilityInput(
                raw_text="API implementation",
                entity_id="capability-api",
                experience_codes=["DELIVERED"],
            ),
        ),
        completeness=InputCompleteness(
            entities_complete=True,
            experience_complete_entity_ids=frozenset({"capability-api"}),
        ),
        constraints=PlanningConstraints(
            target_by=REFERENCE_AT + timedelta(days=30),
            available_hours_per_week=4,
        ),
        calendar=(
            PlanningSlot(
                starts_at=REFERENCE_AT,
                ends_at=REFERENCE_AT + timedelta(hours=1),
                capacity_week_key="2026-W39",
            ),
        ),
        candidate_availability=(),
        solver_settings=SolverSettings(time_limit_seconds=5.0),
    )
    factory = SnapshotBackedAnalysisContextFactory(
        source=StaticInputSource(inputs, expected_reference_at=REFERENCE_AT),
        snapshot_reader=StaticSnapshotReader(_snapshot()),
        configuration=ContextSnapshotConfiguration(
            basis_version="reviewed-v1",
            release_id="release-reviewed-v1",
        ),
        now=lambda: REFERENCE_AT,
    )
    command = AnalysisRequestCommand(
        goal_id=GOAL_ID,
        expected_profile_version=3,
        basis_type="EDITORIAL",
    )

    # When
    context = anyio.run(factory.create_context, USER_ID, command)

    # Then
    assert context.user_id == USER_ID
    assert context.profile_version == 3
    assert context.goal_id == GOAL_ID
    assert context.snapshot_selection.occupation_id == "BACKEND_DEVELOPER"
    assert context.snapshot_selection.basis_version == "reviewed-v1"
    assert context.snapshot_selection.release_id == "release-reviewed-v1"
    assert context.snapshot_selection.source == "local_json"
    assert context.capabilities[0].entity_id == "capability-api"
    assert context.reference_at == REFERENCE_AT
    assert context.planning_started_at == REFERENCE_AT
    assert context.to_context().solver_settings.time_limit_seconds == 5.0

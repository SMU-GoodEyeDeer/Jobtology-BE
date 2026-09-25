from dataclasses import dataclass
from datetime import UTC, datetime

from jobtology_be.corpus.snapshot import PublishedCorpusSnapshot, PublishedSnapshotSelection
from jobtology_be.modules.analyses.editorial_models import (
    EditorialReleaseMetadata,
    EditorialRequirement,
    ReleaseState,
    RequirementNecessity,
)
from jobtology_be.modules.profiles.normalizer import CapabilityCatalogEntry
from jobtology_be.planning.candidate_models import ActivityTemplate, KnownKrwCost

DELIVERY_REQUIREMENT_KEY = "api-delivery"
FOUNDATION_REQUIREMENT_KEY = "api-foundation"
DELIVERY_LABEL = "Delivered Python API"
FOUNDATION_LABEL = "Python API foundations"
DELIVERY_CODE = "DELIVERED"
FOUNDATION_CODE = "REVIEWED"
CAPABILITY_ENTITY_ID = "python-api"
RELEASE_ID = "release-reviewed-v1"
REFERENCE_AT = datetime(2026, 9, 22, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class DerivedCompletionCapability:
    raw_text: str
    entity_id: str | None
    requirement_keys: tuple[str, ...]
    experience_codes: tuple[str, ...]
    provenance: tuple[tuple[str, str, tuple[str, ...]], ...]
    lifecycle: str


@dataclass(frozen=True, slots=True)
class StaticSnapshotReader:
    snapshot: PublishedCorpusSnapshot

    async def get_snapshot(self, selection: PublishedSnapshotSelection) -> PublishedCorpusSnapshot:
        return self.snapshot.require_selection(selection)


def shared_entity_snapshot() -> PublishedCorpusSnapshot:
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
                aliases=frozenset({DELIVERY_LABEL, FOUNDATION_LABEL}),
            ),
        ),
        allowed_experience_codes=frozenset({DELIVERY_CODE, FOUNDATION_CODE}),
        requirements=(
            EditorialRequirement(
                requirement_key=FOUNDATION_REQUIREMENT_KEY,
                label=FOUNDATION_LABEL,
                necessity=RequirementNecessity.REQUIRED,
                entity_id=CAPABILITY_ENTITY_ID,
                required_experience_codes=frozenset({FOUNDATION_CODE}),
                support_refs=frozenset({"review:api-foundation"}),
            ),
            EditorialRequirement(
                requirement_key=DELIVERY_REQUIREMENT_KEY,
                label=DELIVERY_LABEL,
                necessity=RequirementNecessity.REQUIRED,
                entity_id=CAPABILITY_ENTITY_ID,
                required_experience_codes=frozenset({DELIVERY_CODE}),
                support_refs=frozenset({"review:api-delivery"}),
            ),
        ),
        templates=(
            ActivityTemplate(
                action_id="api-project",
                revision=1,
                title="API project",
                estimated_hours=1,
                outcome_requirement_keys=frozenset(
                    {DELIVERY_REQUIREMENT_KEY, FOUNDATION_REQUIREMENT_KEY}
                ),
                prerequisite_action_ids=(),
                completion_criteria=("Publish an API",),
                support_refs=frozenset({"template:api-project"}),
                cost=KnownKrwCost(krw=0),
                is_foundational=False,
            ),
        ),
    )

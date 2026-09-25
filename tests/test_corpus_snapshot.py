import json
from datetime import UTC, datetime

import pytest

from jobtology_be.corpus.local_snapshot import LocalJsonPublishedCorpusSnapshotReader
from jobtology_be.corpus.snapshot import (
    FixtureSnapshotForbiddenError,
    PublishedCorpusSnapshot,
    PublishedSnapshotIdentityMismatchError,
    PublishedSnapshotSelection,
)
from jobtology_be.modules.analyses.editorial_models import (
    EditorialReleaseMetadata,
    EditorialRequirement,
    ReleaseState,
    RequirementNecessity,
    UnusableReleaseError,
)
from jobtology_be.modules.profiles.normalizer import CapabilityCatalogEntry
from jobtology_be.planning.candidate_models import ActivityTemplate, KnownKrwCost

REFERENCE_TIME = datetime(2026, 9, 22, 12, tzinfo=UTC)


def _snapshot(*, release_state: ReleaseState, is_fixture: bool) -> PublishedCorpusSnapshot:
    return PublishedCorpusSnapshot(
        occupation_id="BACKEND_DEVELOPER",
        basis_version="reviewed-v1",
        release=EditorialReleaseMetadata(
            release_id=None if is_fixture else "release-reviewed-v1",
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


def test_snapshot_preserves_pinned_editorial_release_when_published() -> None:
    # Given
    snapshot = _snapshot(release_state=ReleaseState.PUBLISHED, is_fixture=False)

    # When
    baseline = snapshot.editorial_baseline()

    # Then
    assert baseline.release.release_id == "release-reviewed-v1"
    assert not baseline.is_fixture
    assert baseline.requirements[0].entity_id == "capability-api"


def test_snapshot_rejects_revoked_release_before_analysis_can_run() -> None:
    # Given / When / Then
    with pytest.raises(UnusableReleaseError):
        _ = _snapshot(release_state=ReleaseState.REVOKED, is_fixture=False)


def test_snapshot_allows_explicit_fixture_label_for_test_inputs() -> None:
    # Given
    snapshot = _snapshot(release_state=ReleaseState.FIXTURE, is_fixture=True)

    # When
    baseline = snapshot.editorial_baseline()

    # Then
    assert baseline.is_fixture
    assert baseline.release.release_id is None


def test_snapshot_rejects_fixture_for_recompute_without_test_opt_in() -> None:
    # Given
    snapshot = _snapshot(release_state=ReleaseState.FIXTURE, is_fixture=True)

    # When / Then
    with pytest.raises(FixtureSnapshotForbiddenError):
        _ = snapshot.baseline_for_recompute(allow_fixture=False)


def test_snapshot_rejects_a_reader_response_with_a_different_pinned_identity() -> None:
    # Given
    snapshot = _snapshot(release_state=ReleaseState.PUBLISHED, is_fixture=False)
    selection = PublishedSnapshotSelection(
        occupation_id="FRONTEND_DEVELOPER",
        basis_version="reviewed-v1",
        release_id="release-reviewed-v1",
    )

    # When / Then
    with pytest.raises(PublishedSnapshotIdentityMismatchError):
        _ = snapshot.require_selection(selection)


@pytest.mark.anyio
async def test_local_json_reader_returns_only_the_exact_published_snapshot(tmp_path) -> None:
    # Given
    path = tmp_path / "published-snapshots.json"
    path.write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "occupation_id": "BACKEND_DEVELOPER",
                        "basis_version": "reviewed-v1",
                        "release": {
                            "release_id": "release-reviewed-v1",
                            "state": "PUBLISHED",
                            "reviewed_at": "2026-09-22T12:00:00+00:00",
                        },
                        "is_fixture": False,
                        "capability_entries": [
                            {"entity_id": "capability-api", "aliases": ["API implementation"]}
                        ],
                        "allowed_experience_codes": ["DELIVERED"],
                        "requirements": [
                            {
                                "requirement_key": "api",
                                "label": "API implementation",
                                "necessity": "REQUIRED",
                                "entity_id": "capability-api",
                                "required_experience_codes": ["DELIVERED"],
                                "support_refs": ["review:api"],
                            }
                        ],
                        "templates": [
                            {
                                "action_id": "api-project",
                                "revision": 1,
                                "title": "API project",
                                "estimated_hours": 8,
                                "outcome_requirement_keys": ["api"],
                                "completion_criteria": ["publish API"],
                                "support_refs": ["template:api-project"],
                                "cost": {"kind": "KNOWN", "krw": 0},
                                "is_foundational": False,
                            }
                        ],
                    }
                ]
            }
        )
    )
    reader = LocalJsonPublishedCorpusSnapshotReader.from_path(path)

    # When
    snapshot = await reader.get_snapshot(
        PublishedSnapshotSelection(
            occupation_id="BACKEND_DEVELOPER",
            basis_version="reviewed-v1",
            release_id="release-reviewed-v1",
        )
    )

    # Then
    assert snapshot.templates[0].outcome_requirement_keys == frozenset({"api"})

from datetime import UTC, datetime
from typing import Literal

import pytest

from jobtology_be.contracts import NormalizedCapability, NormalizedProfile
from jobtology_be.modules.analyses.editorial import EditorialGapAnalyzer
from jobtology_be.modules.analyses.editorial_models import (
    EditorialAnalysisRequest,
    EditorialBaseline,
    EditorialReleaseMetadata,
    EditorialRequirement,
    InputCompleteness,
    MissingPublishedReleaseIdError,
    NaiveReviewedAtError,
    ReleaseState,
    RequirementNecessity,
    SatisfactionStatus,
)

REFERENCE_TIME = datetime(2026, 9, 22, 12, tzinfo=UTC)
REQUIREMENT = EditorialRequirement(
    requirement_key="project",
    label="project",
    necessity=RequirementNecessity.REQUIRED,
    entity_id="capability-project",
    required_experience_codes=frozenset({"DELIVERED"}),
    support_refs=frozenset({"review:project"}),
)


def published_baseline(
    release_id: str = "release-reviewed-v1", reviewed_at: datetime = REFERENCE_TIME
) -> EditorialBaseline:
    return EditorialBaseline(
        occupation_id="BACKEND_DEVELOPER",
        basis_version="reviewed-v1",
        release=EditorialReleaseMetadata(
            release_id=release_id, state=ReleaseState.PUBLISHED, reviewed_at=reviewed_at
        ),
        is_fixture=False,
        requirements=(REQUIREMENT,),
    )


@pytest.mark.parametrize("resolution", ["UNRESOLVED", "AMBIGUOUS"])
def test_matching_id_on_non_resolved_capability_never_receives_credit(
    resolution: Literal["UNRESOLVED", "AMBIGUOUS"],
) -> None:
    # Given
    capability = NormalizedCapability(
        raw_text="project",
        entity_id="capability-project",
        experience_codes=["DELIVERED"],
        resolution=resolution,
    )
    profile = NormalizedProfile(user_id="user-1", profile_version=3, capabilities=[capability])
    request = EditorialAnalysisRequest(
        analysis_id="analysis-1",
        occupation_id="BACKEND_DEVELOPER",
        basis_version="reviewed-v1",
        reference_at=REFERENCE_TIME,
    )

    # When
    analysis = EditorialGapAnalyzer(published_baseline()).evaluate(
        profile, request, InputCompleteness(entities_complete=True)
    )

    # Then
    assert analysis.requirements[0].status is SatisfactionStatus.UNMET
    assert analysis.requirements[0].provenance == frozenset()
    assert analysis.required_coverage.score == 0


@pytest.mark.parametrize(
    ("release_id", "reviewed_at", "error_type"),
    [
        ("   ", REFERENCE_TIME, MissingPublishedReleaseIdError),
        ("release-reviewed-v1", REFERENCE_TIME.replace(tzinfo=None), NaiveReviewedAtError),
    ],
)
def test_published_baseline_rejects_blank_release_id_or_naive_reviewed_time(
    release_id: str, reviewed_at: datetime, error_type: type[Exception]
) -> None:
    # Given / When / Then
    with pytest.raises(error_type):
        _ = published_baseline(release_id=release_id, reviewed_at=reviewed_at)

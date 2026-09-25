from datetime import UTC, datetime

import pytest

from jobtology_be.contracts import NormalizedCapability, NormalizedProfile
from jobtology_be.modules.analyses.editorial import EditorialGapAnalyzer
from jobtology_be.modules.analyses.editorial_models import (
    BaselineMismatchError,
    DuplicateRequirementKeyError,
    EditorialAnalysisRequest,
    EditorialBaseline,
    EditorialReleaseMetadata,
    EditorialRequirement,
    InputCompleteness,
    MissingRequirementEvidenceError,
    ReleaseState,
    RequirementNecessity,
    SatisfactionStatus,
    UnavailableReason,
    UnusableReleaseError,
)

REFERENCE_TIME = datetime(2026, 9, 22, 12, tzinfo=UTC)


def requirement(
    key: str,
    entity_id: str,
    necessity: RequirementNecessity,
    experience_codes: frozenset[str] | None = None,
) -> EditorialRequirement:
    return EditorialRequirement(
        requirement_key=key,
        label=key,
        necessity=necessity,
        entity_id=entity_id,
        required_experience_codes=frozenset() if experience_codes is None else experience_codes,
        support_refs=frozenset({f"review:{key}"}),
    )


def baseline(
    requirements: tuple[EditorialRequirement, ...],
    *,
    occupation_id: str = "BACKEND_DEVELOPER",
    basis_version: str = "reviewed-v1",
    release_state: ReleaseState = ReleaseState.PUBLISHED,
    is_fixture: bool = False,
) -> EditorialBaseline:
    return EditorialBaseline(
        occupation_id=occupation_id,
        basis_version=basis_version,
        release=EditorialReleaseMetadata(
            release_id=None if is_fixture else "release-reviewed-v1",
            state=release_state,
            reviewed_at=REFERENCE_TIME,
        ),
        is_fixture=is_fixture,
        requirements=requirements,
    )


def profile(*capabilities: NormalizedCapability) -> NormalizedProfile:
    return NormalizedProfile(user_id="user-1", profile_version=3, capabilities=list(capabilities))


def resolved(entity_id: str, experience_codes: list[str] | None = None) -> NormalizedCapability:
    return NormalizedCapability(
        raw_text=entity_id,
        entity_id=entity_id,
        experience_codes=[] if experience_codes is None else experience_codes,
        resolution="RESOLVED",
    )


def request(
    *,
    occupation_id: str = "BACKEND_DEVELOPER",
    basis_version: str = "reviewed-v1",
) -> EditorialAnalysisRequest:
    return EditorialAnalysisRequest(
        analysis_id="analysis-replay-1",
        occupation_id=occupation_id,
        basis_version=basis_version,
        reference_at=REFERENCE_TIME,
    )


def test_beginner_is_unmet_when_entity_input_is_complete() -> None:
    # Given
    engine = EditorialGapAnalyzer(
        baseline(
            (
                requirement("fundamentals", "capability-fundamentals", RequirementNecessity.REQUIRED),
                requirement("project", "capability-project", RequirementNecessity.REQUIRED),
                requirement("preparation", "capability-preparation", RequirementNecessity.PREFERRED),
            )
        )
    )

    # When
    result = engine.evaluate(profile(), request(), InputCompleteness(entities_complete=True))

    # Then
    assert [item.status for item in result.requirements] == [
        SatisfactionStatus.UNMET,
        SatisfactionStatus.UNMET,
        SatisfactionStatus.UNMET,
    ]
    assert result.required_coverage.score == 0
    assert result.preferred_coverage.score == 0


def test_fundamentals_only_cover_their_requirement() -> None:
    # Given
    engine = EditorialGapAnalyzer(
        baseline(
            (
                requirement("fundamentals", "capability-fundamentals", RequirementNecessity.REQUIRED),
                requirement("project", "capability-project", RequirementNecessity.REQUIRED),
                requirement("preparation", "capability-preparation", RequirementNecessity.PREFERRED),
            )
        )
    )

    # When
    result = engine.evaluate(
        profile(resolved("capability-fundamentals")),
        request(),
        InputCompleteness(entities_complete=True),
    )

    # Then
    assert [item.status for item in result.requirements] == [
        SatisfactionStatus.SATISFIED,
        SatisfactionStatus.UNMET,
        SatisfactionStatus.UNMET,
    ]
    assert result.required_coverage.matched_count == 1
    assert result.required_coverage.total_count == 2
    assert result.required_coverage.score == 0.5


def test_project_experience_requires_all_declared_codes() -> None:
    # Given
    engine = EditorialGapAnalyzer(
        baseline(
            (
                requirement("fundamentals", "capability-fundamentals", RequirementNecessity.REQUIRED),
                requirement(
                    "project",
                    "capability-project",
                    RequirementNecessity.REQUIRED,
                    frozenset({"DELIVERED"}),
                ),
            )
        )
    )
    completeness = InputCompleteness(
        entities_complete=True,
        experience_complete_entity_ids=frozenset({"capability-project"}),
    )

    # When
    result = engine.evaluate(
        profile(resolved("capability-fundamentals"), resolved("capability-project", ["DELIVERED"])),
        request(),
        completeness,
    )

    # Then
    assert [item.status for item in result.requirements] == [
        SatisfactionStatus.SATISFIED,
        SatisfactionStatus.SATISFIED,
    ]
    assert (
        result.required_coverage.score == 1
        and result.preferred_coverage.reason is UnavailableReason.NO_REQUIREMENTS
    )


def test_preparation_is_preferred_coverage_not_required_coverage() -> None:
    # Given
    engine = EditorialGapAnalyzer(
        baseline(
            (
                requirement("fundamentals", "capability-fundamentals", RequirementNecessity.REQUIRED),
                requirement("preparation", "capability-preparation", RequirementNecessity.PREFERRED),
            )
        )
    )

    # When
    result = engine.evaluate(
        profile(resolved("capability-fundamentals"), resolved("capability-preparation")),
        request(),
        InputCompleteness(entities_complete=True),
    )

    # Then
    assert result.required_coverage.score == 1
    assert result.preferred_coverage.score == 1
    assert result.required_coverage.total_count == 1
    assert result.preferred_coverage.total_count == 1


def test_unresolved_and_ambiguous_text_do_not_receive_credit() -> None:
    # Given
    engine = EditorialGapAnalyzer(
        baseline((requirement("project", "capability-project", RequirementNecessity.REQUIRED),))
    )
    unresolved = NormalizedCapability(
        raw_text="project", entity_id=None, experience_codes=[], resolution="UNRESOLVED"
    )
    ambiguous = NormalizedCapability(
        raw_text="project", entity_id=None, experience_codes=[], resolution="AMBIGUOUS"
    )

    # When
    result = engine.evaluate(
        profile(unresolved, ambiguous), request(), InputCompleteness(entities_complete=True)
    )

    # Then
    assert result.requirements[0].status is SatisfactionStatus.UNMET
    assert result.required_coverage.score == 0


def test_matching_entity_without_complete_experience_input_needs_input() -> None:
    # Given
    engine = EditorialGapAnalyzer(
        baseline(
            (
                requirement(
                    "project",
                    "capability-project",
                    RequirementNecessity.REQUIRED,
                    frozenset({"DELIVERED"}),
                ),
            )
        )
    )

    # When
    result = engine.evaluate(
        profile(resolved("capability-project")), request(), InputCompleteness(entities_complete=True)
    )

    # Then
    assert result.requirements[0].status is SatisfactionStatus.NEEDS_INPUT
    assert result.requirements[0].provenance == frozenset({"SELF_REPORTED"})


def test_incomplete_entity_input_is_needs_input_not_unmet() -> None:
    # Given
    engine = EditorialGapAnalyzer(
        baseline((requirement("project", "capability-project", RequirementNecessity.REQUIRED),))
    )

    # When
    result = engine.evaluate(profile(), request(), InputCompleteness(entities_complete=False))

    # Then
    assert result.requirements[0].status is SatisfactionStatus.NEEDS_INPUT


def test_published_baseline_rejects_missing_evidence_and_duplicate_keys() -> None:
    # Given
    missing_evidence = EditorialRequirement(
        requirement_key="project",
        label="project",
        necessity=RequirementNecessity.REQUIRED,
        entity_id="capability-project",
        required_experience_codes=frozenset(),
        support_refs=frozenset(),
    )
    duplicated = requirement("project", "capability-project", RequirementNecessity.REQUIRED)

    # When / Then
    with pytest.raises(MissingRequirementEvidenceError):
        _ = baseline((missing_evidence,))
    with pytest.raises(DuplicateRequirementKeyError):
        _ = baseline((duplicated, duplicated))


@pytest.mark.parametrize("release_state", [ReleaseState.UNPUBLISHED, ReleaseState.REVOKED])
def test_unpublished_or_revoked_release_is_rejected(release_state: ReleaseState) -> None:
    # Given / When / Then
    with pytest.raises(UnusableReleaseError):
        _ = baseline(
            (requirement("project", "capability-project", RequirementNecessity.REQUIRED),),
            release_state=release_state,
        )


@pytest.mark.parametrize(
    ("occupation_id", "basis_version"),
    [("FRONTEND_DEVELOPER", "reviewed-v1"), ("BACKEND_DEVELOPER", "reviewed-v2")],
)
def test_mismatched_role_or_basis_version_is_rejected(
    occupation_id: str, basis_version: str
) -> None:
    # Given
    engine = EditorialGapAnalyzer(
        baseline((requirement("project", "capability-project", RequirementNecessity.REQUIRED),))
    )

    # When / Then
    with pytest.raises(BaselineMismatchError):
        _ = engine.evaluate(profile(), request(occupation_id=occupation_id, basis_version=basis_version), InputCompleteness(entities_complete=True))


def test_replay_preserves_explicit_identifiers_reference_time_and_empty_state() -> None:
    # Given
    engine = EditorialGapAnalyzer(
        baseline((), release_state=ReleaseState.FIXTURE, is_fixture=True)
    )
    analysis_request = request()
    completeness = InputCompleteness(entities_complete=True)

    # When
    first = engine.evaluate(profile(), analysis_request, completeness)
    second = engine.evaluate(profile(), analysis_request, completeness)

    # Then
    assert first == second
    assert first.analysis_id == "analysis-replay-1" and first.is_fixture
    assert first.reference_at == REFERENCE_TIME
    assert first.analysis_status.value == "INSUFFICIENT_DATA"
    assert first.required_coverage.score is None
    assert first.required_coverage.reason is UnavailableReason.EMPTY_BASELINE

from datetime import UTC, datetime

import pytest

from jobtology_be.modules.analyses.editorial_models import (
    AnalysisStatus,
    Coverage,
    CoverageAvailability,
    EditorialAnalysis,
    RequirementEvaluation,
    RequirementNecessity,
    SatisfactionStatus,
)
from jobtology_be.planning.candidate_models import (
    ActivityTemplate,
    InsufficientAnalysisBlocker,
    KnownKrwCost,
    NeedsInputBlocker,
    NoActivitiesBlocker,
    UnknownKrwCost,
)
from jobtology_be.planning.candidates import CandidateGenerator, UnknownOutcomeRequirementError

REFERENCE_TIME = datetime(2026, 9, 22, 12, tzinfo=UTC)


def evaluation(requirement_key: str, status: SatisfactionStatus) -> RequirementEvaluation:
    return RequirementEvaluation(
        requirement_key=requirement_key,
        label=requirement_key,
        necessity=RequirementNecessity.REQUIRED,
        status=status,
        provenance=frozenset({"SYNTHETIC"}),
        support_refs=frozenset({"synthetic:requirement"}),
    )


def analysis(
    *requirements: RequirementEvaluation,
    status: AnalysisStatus = AnalysisStatus.READY,
) -> EditorialAnalysis:
    coverage = Coverage(
        availability=CoverageAvailability.AVAILABLE,
        reason=None,
        score=0.0,
        matched_count=0,
        total_count=len(requirements),
        score_method="SYNTHETIC",
    )
    return EditorialAnalysis(
        analysis_id="analysis-1",
        profile_version=4,
        occupation_id="BACKEND_DEVELOPER",
        basis_version="reviewed-v1",
        corpus_release_id=None,
        is_fixture=True,
        reference_at=REFERENCE_TIME,
        analysis_status=status,
        requirements=requirements,
        required_coverage=coverage,
        preferred_coverage=coverage,
    )


def template(
    action_id: str,
    outcomes: frozenset[str],
    prerequisites: tuple[str, ...] = (),
    foundational: bool = False,
    cost: KnownKrwCost | UnknownKrwCost | None = None,
) -> ActivityTemplate:
    return ActivityTemplate(
        action_id=action_id,
        revision=1,
        title=f"Synthetic {action_id}",
        estimated_hours=3,
        outcome_requirement_keys=outcomes,
        prerequisite_action_ids=prerequisites,
        completion_criteria=("synthetic completion",),
        support_refs=frozenset({"synthetic:template"}),
        cost=KnownKrwCost(krw=0) if cost is None else cost,
        is_foundational=foundational,
    )


def test_build_selects_every_alternative_for_unmet_requirement_with_deduplicated_prerequisite() -> None:
    # Given
    candidate_set = CandidateGenerator(
        (
            template(
                "api-route",
                frozenset({"api"}),
                ("fundamentals",),
                cost=KnownKrwCost(krw=50_000),
            ),
            template(
                "sql-route",
                frozenset({"api"}),
                ("fundamentals",),
                cost=KnownKrwCost(krw=0),
            ),
            template("fundamentals", frozenset({"foundation"})),
        )
    ).build(
        analysis(
            evaluation("api", SatisfactionStatus.UNMET),
            evaluation("foundation", SatisfactionStatus.SATISFIED),
        )
    )

    # When
    action_ids = tuple(candidate.action_id for candidate in candidate_set.candidates)

    # Then
    assert action_ids == ("fundamentals", "api-route", "sql-route")
    assert candidate_set.analysis_id == "analysis-1"
    assert candidate_set.profile_version == 4
    assert candidate_set.occupation_id == "BACKEND_DEVELOPER"
    assert candidate_set.basis_version == "reviewed-v1"
    assert candidate_set.corpus_release_id is None
    assert candidate_set.is_fixture


def test_build_returns_needs_input_as_blocker_without_selecting_its_activity() -> None:
    # Given
    candidate_set = CandidateGenerator((template("clarify-api", frozenset({"api"})),)).build(
        analysis(evaluation("api", SatisfactionStatus.NEEDS_INPUT))
    )

    # When
    blockers = candidate_set.blockers

    # Then
    assert candidate_set.candidates == ()
    assert blockers == (
        NeedsInputBlocker(
            requirement_key="api",
            label="api",
            support_refs=frozenset({"synthetic:requirement"}),
        ),
    )


def test_build_returns_no_activities_blocker_for_an_unmet_requirement_without_template() -> None:
    # Given
    candidate_set = CandidateGenerator(()).build(
        analysis(evaluation("api", SatisfactionStatus.UNMET))
    )

    # When
    blockers = candidate_set.blockers

    # Then
    assert candidate_set.candidates == ()
    assert blockers == (
        NoActivitiesBlocker(
            requirement_key="api",
            label="api",
            support_refs=frozenset({"synthetic:requirement"}),
        ),
    )


def test_build_skips_only_foundational_prerequisites_with_nonempty_satisfied_outcomes() -> None:
    # Given
    candidate_set = CandidateGenerator(
        (
            template("route", frozenset({"api"}), ("empty-foundation", "foundation")),
            template("foundation", frozenset({"foundation"}), foundational=True),
            template("empty-foundation", frozenset(), foundational=True),
        )
    ).build(
        analysis(
            evaluation("api", SatisfactionStatus.UNMET),
            evaluation("foundation", SatisfactionStatus.SATISFIED),
        )
    )

    # When
    candidates = candidate_set.candidates

    # Then
    assert tuple(candidate.action_id for candidate in candidates) == (
        "empty-foundation",
        "route",
    )
    assert candidates[-1].prerequisite_action_ids == ("empty-foundation",)
    assert candidates[-1].fulfilled_prerequisite_action_ids == ("foundation",)


def test_build_is_deterministic_when_injected_template_input_order_changes() -> None:
    # Given
    templates = (
        template("route-b", frozenset({"api"}), ("foundation",)),
        template("foundation", frozenset({"foundation"})),
        template("route-a", frozenset({"api"}), ("foundation",)),
    )
    analyzed = analysis(
        evaluation("api", SatisfactionStatus.UNMET),
        evaluation("foundation", SatisfactionStatus.SATISFIED),
    )

    # When
    forward = CandidateGenerator(templates).build(analyzed)
    reverse = CandidateGenerator(tuple(reversed(templates))).build(analyzed)

    # Then
    assert forward == reverse


def test_build_blocks_insufficient_analysis_without_treating_it_as_unmet() -> None:
    # Given
    candidate_set = CandidateGenerator((template("route", frozenset({"api"})),)).build(
        analysis(
            evaluation("other", SatisfactionStatus.UNMET),
            status=AnalysisStatus.INSUFFICIENT_DATA,
        )
    )

    # When
    blockers = candidate_set.blockers

    # Then
    assert candidate_set.candidates == ()
    assert blockers == (InsufficientAnalysisBlocker(analysis_id="analysis-1"),)


def test_build_rejects_template_outcomes_absent_from_ready_analysis_baseline() -> None:
    # Given
    generator = CandidateGenerator((template("route", frozenset({"api"})),))

    # When / Then
    with pytest.raises(UnknownOutcomeRequirementError):
        _ = generator.build(analysis(evaluation("other", SatisfactionStatus.UNMET)))


def test_known_zero_cost_is_distinct_from_explicitly_unknown_cost() -> None:
    # Given
    known_zero = KnownKrwCost(krw=0)
    unknown = UnknownKrwCost()

    # When
    known_amount = known_zero.krw

    # Then
    assert known_amount == 0
    assert unknown == UnknownKrwCost()

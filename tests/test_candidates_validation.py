import pytest

from jobtology_be.planning.candidate_models import (
    ActivityTemplate,
    InvalidTemplateEffortError,
    KnownKrwCost,
)
from jobtology_be.planning.candidates import (
    AmbiguousTemplateRevisionError,
    CandidateGenerator,
    DuplicateTemplateRevisionError,
    MissingPrerequisiteTemplateError,
    PrerequisiteCycleError,
)


def template(
    action_id: str,
    revision: int = 1,
    outcomes: frozenset[str] | None = None,
    prerequisites: tuple[str, ...] = (),
    estimated_hours: int = 2,
) -> ActivityTemplate:
    return ActivityTemplate(
        action_id=action_id,
        revision=revision,
        title=f"Synthetic {action_id}",
        estimated_hours=estimated_hours,
        outcome_requirement_keys=frozenset({"api"}) if outcomes is None else outcomes,
        prerequisite_action_ids=prerequisites,
        completion_criteria=("synthetic completion",),
        support_refs=frozenset({"synthetic:template"}),
        cost=KnownKrwCost(krw=0),
        is_foundational=False,
    )


@pytest.mark.parametrize(
    ("templates", "error_type"),
    [
        (
            (template("route", revision=1), template("route", revision=1)),
            DuplicateTemplateRevisionError,
        ),
        (
            (template("route", revision=1), template("route", revision=2)),
            AmbiguousTemplateRevisionError,
        ),
    ],
)
def test_constructor_rejects_duplicate_or_ambiguous_template_revisions(
    templates: tuple[ActivityTemplate, ...], error_type: type[Exception]
) -> None:
    # Given
    injected_templates = templates

    # When / Then
    with pytest.raises(error_type):
        _ = CandidateGenerator(injected_templates)


def test_constructor_rejects_missing_prerequisite_template() -> None:
    # Given
    injected_templates = (template("route", prerequisites=("missing",)),)

    # When / Then
    with pytest.raises(MissingPrerequisiteTemplateError):
        _ = CandidateGenerator(injected_templates)


def test_constructor_rejects_prerequisite_cycle() -> None:
    # Given
    injected_templates = (
        template("a", prerequisites=("b",)),
        template("b", prerequisites=("a",)),
    )

    # When / Then
    with pytest.raises(PrerequisiteCycleError):
        _ = CandidateGenerator(injected_templates)


def test_template_rejects_nonpositive_effort() -> None:
    # Given
    activity = lambda: template("route", estimated_hours=0)

    # When / Then
    with pytest.raises(InvalidTemplateEffortError):
        _ = activity()

from datetime import UTC, datetime, timedelta

import pytest

from jobtology_be.modules.analyses.editorial_models import (
    EditorialRequirement,
    RequirementNecessity,
)
from jobtology_be.planning.candidate_models import ActivityTemplate, KnownKrwCost
from jobtology_be.planning.solver_models import ScheduledRouteStep
from jobtology_be.workers.outcomes import PinnedOutcomeResolutionError, PinnedOutcomeResolver

REFERENCE_TIME = datetime(2026, 9, 22, 12, tzinfo=UTC)


def _requirement() -> EditorialRequirement:
    return EditorialRequirement(
        requirement_key="api",
        label="API implementation",
        necessity=RequirementNecessity.REQUIRED,
        entity_id="capability-api",
        required_experience_codes=frozenset({"DELIVERED"}),
        support_refs=frozenset({"review:api"}),
    )


def _template(*, outcome_requirement_keys: frozenset[str]) -> ActivityTemplate:
    return ActivityTemplate(
        action_id="api-project",
        revision=1,
        title="API project",
        estimated_hours=1,
        outcome_requirement_keys=outcome_requirement_keys,
        prerequisite_action_ids=(),
        completion_criteria=("publish API",),
        support_refs=frozenset({"template:api-project"}),
        cost=KnownKrwCost(krw=0),
        is_foundational=False,
    )


def _step() -> ScheduledRouteStep:
    return ScheduledRouteStep(
        step_key="api-project:1",
        action_id="api-project",
        template_revision=1,
        title="API project",
        estimated_hours=1,
        prerequisite_step_keys=(),
        outcome_requirement_keys=("api",),
        completion_criteria=("publish API",),
        support_refs=("template:api-project",),
        reason_codes=(),
        planned_start_at=REFERENCE_TIME,
        planned_end_at=REFERENCE_TIME + timedelta(hours=1),
        slot_allocations=(),
    )


def test_pinned_outcome_resolver_serializes_canonical_capability_outcomes() -> None:
    # Given
    resolver = PinnedOutcomeResolver(
        requirements=(_requirement(),),
        templates=(_template(outcome_requirement_keys=frozenset({"api"})),),
    )

    # When
    outcomes = resolver.serialize(_step())

    # Then
    assert outcomes == (
        {
            "requirement_key": "api",
            "entity_id": "capability-api",
            "raw_text": "API implementation",
            "experience_codes": ("DELIVERED",),
        },
    )


def test_pinned_outcome_resolver_rejects_step_keys_not_declared_by_template() -> None:
    # Given
    resolver = PinnedOutcomeResolver(
        requirements=(_requirement(),),
        templates=(_template(outcome_requirement_keys=frozenset()),),
    )

    # When / Then
    with pytest.raises(PinnedOutcomeResolutionError):
        _ = resolver.serialize(_step())

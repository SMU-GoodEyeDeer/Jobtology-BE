from collections.abc import Mapping
from dataclasses import dataclass
from typing import override

from jobtology_be.contracts import RoadmapOutcome
from jobtology_be.infrastructure.persistence.contracts import JsonValue
from jobtology_be.modules.analyses.editorial_models import EditorialRequirement
from jobtology_be.planning.candidate_models import ActivityTemplate
from jobtology_be.planning.solver_models import ScheduledRouteStep


@dataclass(frozen=True, slots=True)
class PinnedOutcomeResolutionError(Exception):
    action_id: str
    template_revision: int
    reason: str

    @override
    def __str__(self) -> str:
        return (
            f"cannot resolve pinned outcomes for {self.action_id}@{self.template_revision}: "
            f"{self.reason}"
        )


@dataclass(frozen=True, slots=True)
class PinnedOutcomeResolver:
    requirements: tuple[EditorialRequirement, ...]
    templates: tuple[ActivityTemplate, ...]

    def serialize(self, step: ScheduledRouteStep) -> tuple[Mapping[str, JsonValue], ...]:
        template = next(
            (
                item
                for item in self.templates
                if item.action_id == step.action_id and item.revision == step.template_revision
            ),
            None,
        )
        if template is None:
            raise PinnedOutcomeResolutionError(
                action_id=step.action_id,
                template_revision=step.template_revision,
                reason="template revision is absent from the pinned snapshot",
            )
        if tuple(sorted(step.outcome_requirement_keys)) != tuple(
            sorted(template.outcome_requirement_keys)
        ):
            raise PinnedOutcomeResolutionError(
                action_id=step.action_id,
                template_revision=step.template_revision,
                reason="scheduled requirement keys differ from the pinned template",
            )
        requirements_by_key = {
            requirement.requirement_key: requirement for requirement in self.requirements
        }
        outcomes: list[Mapping[str, JsonValue]] = []
        for requirement_key in step.outcome_requirement_keys:
            requirement = requirements_by_key.get(requirement_key)
            if requirement is None:
                raise PinnedOutcomeResolutionError(
                    action_id=step.action_id,
                    template_revision=step.template_revision,
                    reason=f"requirement key {requirement_key!r} is absent from the pinned snapshot",
                )
            outcome = RoadmapOutcome(
                requirement_key=requirement.requirement_key,
                entity_id=requirement.entity_id,
                raw_text=requirement.label,
                experience_codes=tuple(sorted(requirement.required_experience_codes)),
            )
            outcomes.append(
                {
                    "requirement_key": outcome.requirement_key,
                    "entity_id": outcome.entity_id,
                    "raw_text": outcome.raw_text,
                    "experience_codes": outcome.experience_codes,
                }
            )
        return tuple(outcomes)

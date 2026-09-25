from collections.abc import Sequence
from dataclasses import dataclass

from jobtology_be.contracts import RoadmapOutcome


@dataclass(frozen=True, slots=True)
class RequirementOutcomeProvenance:
    requirement_key: str
    raw_text: str
    experience_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalCompletionOutcome:
    entity_id: str
    raw_text: str
    requirement_keys: tuple[str, ...]
    experience_codes: tuple[str, ...]
    provenance: tuple[RequirementOutcomeProvenance, ...]


def canonical_completion_outcomes(
    outcomes: Sequence[RoadmapOutcome],
) -> tuple[CanonicalCompletionOutcome, ...]:
    by_entity_id: dict[str, list[RoadmapOutcome]] = {}
    for outcome in outcomes:
        by_entity_id.setdefault(outcome.entity_id, []).append(outcome)
    canonical_outcomes: list[CanonicalCompletionOutcome] = []
    for entity_id in sorted(by_entity_id):
        provenance = tuple(
            RequirementOutcomeProvenance(
                requirement_key=outcome.requirement_key,
                raw_text=outcome.raw_text,
                experience_codes=tuple(sorted(set(outcome.experience_codes))),
            )
            for outcome in sorted(
                by_entity_id[entity_id],
                key=lambda outcome: (
                    outcome.requirement_key,
                    outcome.raw_text,
                    tuple(sorted(set(outcome.experience_codes))),
                ),
            )
        )
        canonical_outcomes.append(
            CanonicalCompletionOutcome(
                entity_id=entity_id,
                raw_text=provenance[0].raw_text,
                requirement_keys=tuple(item.requirement_key for item in provenance),
                experience_codes=tuple(
                    sorted({code for item in provenance for code in item.experience_codes})
                ),
                provenance=provenance,
            )
        )
    return tuple(canonical_outcomes)

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, assert_never

from pydantic import BaseModel, ConfigDict

from jobtology_be.editorial.models import (
    DraftActivity,
    DraftCatalog,
    DraftOccupation,
    DraftRequirement,
    KnownCost,
    UnknownCost,
)


def load_drafts(path: Path) -> DraftCatalog:
    """Import the designated local draft JSON; never query or mutate the published corpus."""
    return DraftCatalog.model_validate_json(path.read_bytes())


class _PublicDraft(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class RequirementRead(_PublicDraft):
    requirement_key: str
    label: str
    necessity: str
    suggested_learning_outcome: str

    @classmethod
    def from_draft(cls, item: DraftRequirement) -> "RequirementRead":
        return cls(
            requirement_key=item.requirement_key,
            label=item.label,
            necessity=item.necessity,
            suggested_learning_outcome=item.suggested_learning_outcome,
        )


class ActivityRead(_PublicDraft):
    action_id: str
    revision: int
    title: str
    activity_type: str
    estimated_hours: int
    outcome_requirement_keys: tuple[str, ...]
    prerequisite_action_ids: tuple[str, ...]
    completion_criteria: tuple[str, ...]
    cost_status: Literal["UNKNOWN", "KNOWN_KRW"]
    known_cost_krw: int | None

    @classmethod
    def from_draft(cls, item: DraftActivity) -> "ActivityRead":
        match item.cost:
            case KnownCost(krw=krw):
                known_cost_krw = krw
            case UnknownCost():
                known_cost_krw = None
            case unreachable:
                assert_never(unreachable)
        return cls(
            action_id=item.action_id,
            revision=item.revision,
            title=item.title,
            activity_type=item.activity_type,
            estimated_hours=item.estimated_hours,
            outcome_requirement_keys=item.outcome_requirement_keys,
            prerequisite_action_ids=item.prerequisite_action_ids,
            completion_criteria=item.completion_criteria,
            cost_status=item.cost.kind,
            known_cost_krw=known_cost_krw,
        )


class OccupationRead(_PublicDraft):
    occupation_id: str
    title: str
    status: Literal["DRAFT"] = "DRAFT"
    analysis_ready: Literal[False] = False
    version: int
    content_sha256: str
    requirements: tuple[RequirementRead, ...]
    activities: tuple[ActivityRead, ...]


@dataclass(frozen=True, slots=True)
class DraftReadService:
    catalog: DraftCatalog

    def list_occupations(self) -> tuple[OccupationRead, ...]:
        return tuple(self._project(item) for item in self.catalog.occupations)

    def get_occupation(self, occupation_id: str) -> OccupationRead | None:
        item = next(
            (item for item in self.catalog.occupations if item.occupation_id == occupation_id), None
        )
        return self._project(item) if item is not None else None

    def _project(self, item: DraftOccupation) -> OccupationRead:
        return OccupationRead(
            occupation_id=item.occupation_id,
            title=item.title,
            version=self.catalog.version,
            content_sha256=self.catalog.content_sha256,
            requirements=tuple(RequirementRead.from_draft(value) for value in item.requirements),
            activities=tuple(ActivityRead.from_draft(value) for value in item.activities),
        )

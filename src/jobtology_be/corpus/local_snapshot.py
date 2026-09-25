"""Local development-only JSON adapter, not the Goldship publication contract."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from jobtology_be.corpus.snapshot import (
    PublishedCorpusSnapshot,
    PublishedSnapshotSelection,
    PublishedSnapshotUnavailableError,
)
from jobtology_be.modules.analyses.editorial_models import (
    EditorialReleaseMetadata,
    EditorialRequirement,
    ReleaseState,
    RequirementNecessity,
)
from jobtology_be.modules.profiles.normalizer import CapabilityCatalogEntry
from jobtology_be.planning.candidate_models import ActivityTemplate, KnownKrwCost, UnknownKrwCost


class _JsonModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class _ReleaseJson(_JsonModel):
    release_id: str | None
    state: ReleaseState
    reviewed_at: datetime | None


class _CapabilityJson(_JsonModel):
    entity_id: str
    aliases: frozenset[str]


class _RequirementJson(_JsonModel):
    requirement_key: str
    label: str
    necessity: RequirementNecessity
    entity_id: str
    required_experience_codes: frozenset[str] = frozenset()
    support_refs: frozenset[str]


class _KnownCostJson(_JsonModel):
    kind: Literal["KNOWN"]
    krw: int = Field(ge=0)


class _UnknownCostJson(_JsonModel):
    kind: Literal["UNKNOWN"]


class _TemplateJson(_JsonModel):
    action_id: str
    revision: int = Field(gt=0)
    title: str
    estimated_hours: int = Field(gt=0)
    outcome_requirement_keys: frozenset[str]
    prerequisite_action_ids: tuple[str, ...] = ()
    completion_criteria: tuple[str, ...]
    support_refs: frozenset[str]
    cost: _KnownCostJson | _UnknownCostJson
    is_foundational: bool


class _SnapshotJson(_JsonModel):
    occupation_id: str
    basis_version: str
    release: _ReleaseJson
    is_fixture: bool
    capability_entries: tuple[_CapabilityJson, ...]
    allowed_experience_codes: frozenset[str]
    requirements: tuple[_RequirementJson, ...]
    templates: tuple[_TemplateJson, ...]

    def to_snapshot(self) -> PublishedCorpusSnapshot:
        return PublishedCorpusSnapshot(
            occupation_id=self.occupation_id,
            basis_version=self.basis_version,
            release=EditorialReleaseMetadata(
                release_id=self.release.release_id,
                state=self.release.state,
                reviewed_at=self.release.reviewed_at,
            ),
            is_fixture=self.is_fixture,
            capability_entries=tuple(
                CapabilityCatalogEntry(entity_id=entry.entity_id, aliases=entry.aliases)
                for entry in self.capability_entries
            ),
            allowed_experience_codes=self.allowed_experience_codes,
            requirements=tuple(
                EditorialRequirement(
                    requirement_key=requirement.requirement_key,
                    label=requirement.label,
                    necessity=requirement.necessity,
                    entity_id=requirement.entity_id,
                    required_experience_codes=requirement.required_experience_codes,
                    support_refs=requirement.support_refs,
                )
                for requirement in self.requirements
            ),
            templates=tuple(
                ActivityTemplate(
                    action_id=template.action_id,
                    revision=template.revision,
                    title=template.title,
                    estimated_hours=template.estimated_hours,
                    outcome_requirement_keys=template.outcome_requirement_keys,
                    prerequisite_action_ids=template.prerequisite_action_ids,
                    completion_criteria=template.completion_criteria,
                    support_refs=template.support_refs,
                    cost=_cost(template.cost),
                    is_foundational=template.is_foundational,
                )
                for template in self.templates
            ),
        )


class _SnapshotDocument(_JsonModel):
    snapshots: tuple[_SnapshotJson, ...]


@dataclass(frozen=True, slots=True)
class LocalJsonPublishedCorpusSnapshotReader:
    snapshots: tuple[PublishedCorpusSnapshot, ...]

    @classmethod
    def from_path(cls, path: Path) -> "LocalJsonPublishedCorpusSnapshotReader":
        document = _SnapshotDocument.model_validate_json(path.read_text())
        return cls(snapshots=tuple(item.to_snapshot() for item in document.snapshots))

    async def get_snapshot(self, selection: PublishedSnapshotSelection) -> PublishedCorpusSnapshot:
        for snapshot in self.snapshots:
            if (
                snapshot.occupation_id == selection.occupation_id
                and snapshot.basis_version == selection.basis_version
                and snapshot.release.release_id == selection.release_id
            ):
                return snapshot.require_selection(selection)
        raise PublishedSnapshotUnavailableError(selection=selection)


def _cost(value: _KnownCostJson | _UnknownCostJson) -> KnownKrwCost | UnknownKrwCost:
    match value:
        case _KnownCostJson(krw=krw):
            return KnownKrwCost(krw=krw)
        case _UnknownCostJson():
            return UnknownKrwCost()

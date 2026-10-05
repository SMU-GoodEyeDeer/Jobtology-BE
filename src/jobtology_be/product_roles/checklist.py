from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import ClassVar, override

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from jobtology_be.corpus.snapshot import PublishedCorpusSnapshot


class _ChecklistJson(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class ChecklistItemDefinition(_ChecklistJson):
    item_id: StrictStr = Field(min_length=1)
    label: StrictStr = Field(min_length=1)
    entity_ids: tuple[StrictStr, ...] = Field(min_length=1)


class ChecklistGroupDefinition(_ChecklistJson):
    label: StrictStr = Field(min_length=1)
    items: tuple[ChecklistItemDefinition, ...] = Field(min_length=1)


class OccupationChecklistDefinition(_ChecklistJson):
    groups: tuple[ChecklistGroupDefinition, ...] = Field(min_length=1)


class ChecklistDocument(_ChecklistJson):
    version: int = Field(ge=1)
    occupations: dict[StrictStr, OccupationChecklistDefinition]


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    item_id: str
    label: str


@dataclass(frozen=True, slots=True)
class ChecklistGroup:
    label: str
    items: tuple[ChecklistItem, ...]


@dataclass(frozen=True, slots=True)
class OccupationChecklist:
    occupation_id: str
    version: int
    groups: tuple[ChecklistGroup, ...]


@dataclass(frozen=True, slots=True)
class ResolvedChecklistUnit:
    item_id: str
    entity_id: str
    raw_text: str


@dataclass(frozen=True, slots=True)
class UnknownChecklistItemError(Exception):
    item_id: str

    @override
    def __str__(self) -> str:
        return f"unknown onboarding checklist item: {self.item_id}"


@dataclass(frozen=True, slots=True)
class ChecklistOccupationUnavailableError(Exception):
    occupation_id: str

    @override
    def __str__(self) -> str:
        return f"no onboarding checklist for occupation: {self.occupation_id}"


def load_checklist_document(path: Traversable | Path) -> ChecklistDocument:
    return ChecklistDocument.model_validate_json(path.read_text())


@dataclass(frozen=True, slots=True)
class OnboardingChecklistCatalog:
    """Project the approved checklist onto the currently published role requirements.

    Links to units that are no longer requirements of the occupation are ignored and items
    left without links are hidden, so a refreshed role release can never be satisfied by a
    stale checklist entry.
    """

    document: ChecklistDocument
    snapshots: Callable[[], tuple[PublishedCorpusSnapshot, ...]]

    def checklist(self, occupation_id: str) -> OccupationChecklist:
        groups = tuple(
            ChecklistGroup(label=group.label, items=items)
            for group in self._definition(occupation_id).groups
            if (
                items := tuple(
                    ChecklistItem(item_id=item.item_id, label=item.label)
                    for item in group.items
                    if self._units(occupation_id, item)
                )
            )
        )
        return OccupationChecklist(
            occupation_id=occupation_id, version=self.document.version, groups=groups
        )

    def resolve(
        self, occupation_id: str, item_ids: tuple[str, ...]
    ) -> tuple[ResolvedChecklistUnit, ...]:
        items = {
            item.item_id: item
            for group in self._definition(occupation_id).groups
            for item in group.items
        }
        resolved: dict[str, ResolvedChecklistUnit] = {}
        for item_id in item_ids:
            item = items.get(item_id)
            units = self._units(occupation_id, item) if item is not None else ()
            if not units:
                raise UnknownChecklistItemError(item_id=item_id)
            for entity_id, label in units:
                resolved.setdefault(
                    entity_id,
                    ResolvedChecklistUnit(item_id=item_id, entity_id=entity_id, raw_text=label),
                )
        return tuple(resolved.values())

    def _definition(self, occupation_id: str) -> OccupationChecklistDefinition:
        definition = self.document.occupations.get(occupation_id)
        if definition is None or self._snapshot(occupation_id) is None:
            raise ChecklistOccupationUnavailableError(occupation_id=occupation_id)
        return definition

    def _snapshot(self, occupation_id: str) -> PublishedCorpusSnapshot | None:
        return next(
            (
                snapshot
                for snapshot in self.snapshots()
                if snapshot.occupation_id == occupation_id and not snapshot.is_fixture
            ),
            None,
        )

    def _units(
        self, occupation_id: str, item: ChecklistItemDefinition
    ) -> tuple[tuple[str, str], ...]:
        snapshot = self._snapshot(occupation_id)
        if snapshot is None:
            return ()
        labels = {requirement.entity_id: requirement.label for requirement in snapshot.requirements}
        return tuple(
            (entity_id, labels[entity_id]) for entity_id in item.entity_ids if entity_id in labels
        )

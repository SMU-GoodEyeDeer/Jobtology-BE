from dataclasses import dataclass
from typing import Protocol, override

from jobtology_be.modules.analyses.editorial_models import (
    EditorialBaseline,
    EditorialReleaseMetadata,
    EditorialRequirement,
)
from jobtology_be.modules.profiles.normalizer import CapabilityCatalogEntry
from jobtology_be.planning.candidate_models import ActivityTemplate
from jobtology_be.planning.candidates import CandidateGenerator


@dataclass(frozen=True, slots=True)
class PublishedSnapshotSelection:
    occupation_id: str
    basis_version: str
    release_id: str

    def __post_init__(self) -> None:
        if not all((self.occupation_id.strip(), self.basis_version.strip(), self.release_id.strip())):
            raise InvalidPublishedSnapshotSelectionError()


@dataclass(frozen=True, slots=True)
class CorpusSnapshotError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class InvalidPublishedSnapshotSelectionError(CorpusSnapshotError):
    @override
    def __str__(self) -> str:
        return "published snapshot selection fields must be non-empty"


@dataclass(frozen=True, slots=True)
class FixtureSnapshotForbiddenError(CorpusSnapshotError):
    release_id: str | None

    @override
    def __str__(self) -> str:
        return "fixture corpus snapshots cannot be used for recomputation"


@dataclass(frozen=True, slots=True)
class PublishedSnapshotUnavailableError(CorpusSnapshotError):
    selection: PublishedSnapshotSelection

    @override
    def __str__(self) -> str:
        return "requested published corpus snapshot is unavailable"


@dataclass(frozen=True, slots=True)
class PublishedSnapshotIdentityMismatchError(CorpusSnapshotError):
    selection: PublishedSnapshotSelection
    occupation_id: str
    basis_version: str
    release_id: str | None

    @override
    def __str__(self) -> str:
        return "published corpus snapshot does not match its requested identity"


@dataclass(frozen=True, slots=True)
class UnknownTemplateOutcomeRequirementError(CorpusSnapshotError):
    action_id: str
    requirement_key: str

    @override
    def __str__(self) -> str:
        return "activity template outcome must reference a requirement in the pinned snapshot"


@dataclass(frozen=True, slots=True)
class PublishedCorpusSnapshot:
    occupation_id: str
    basis_version: str
    release: EditorialReleaseMetadata
    is_fixture: bool
    capability_entries: tuple[CapabilityCatalogEntry, ...]
    allowed_experience_codes: frozenset[str]
    requirements: tuple[EditorialRequirement, ...]
    templates: tuple[ActivityTemplate, ...]

    def __post_init__(self) -> None:
        _ = self.editorial_baseline()
        _ = CandidateGenerator(self.templates)
        requirement_keys = frozenset(requirement.requirement_key for requirement in self.requirements)
        for template in self.templates:
            missing_keys = template.outcome_requirement_keys - requirement_keys
            if missing_keys:
                raise UnknownTemplateOutcomeRequirementError(
                    action_id=template.action_id,
                    requirement_key=min(missing_keys),
                )

    def editorial_baseline(self) -> EditorialBaseline:
        return EditorialBaseline(
            occupation_id=self.occupation_id,
            basis_version=self.basis_version,
            release=self.release,
            is_fixture=self.is_fixture,
            requirements=self.requirements,
        )

    def baseline_for_recompute(self, *, allow_fixture: bool) -> EditorialBaseline:
        baseline = self.editorial_baseline()
        if baseline.is_fixture and not allow_fixture:
            raise FixtureSnapshotForbiddenError(release_id=baseline.release.release_id)
        return baseline

    def require_selection(self, selection: PublishedSnapshotSelection) -> "PublishedCorpusSnapshot":
        if (
            selection.occupation_id != self.occupation_id
            or selection.basis_version != self.basis_version
            or selection.release_id != self.release.release_id
        ):
            raise PublishedSnapshotIdentityMismatchError(
                selection=selection,
                occupation_id=self.occupation_id,
                basis_version=self.basis_version,
                release_id=self.release.release_id,
            )
        return self


class PublishedCorpusSnapshotReader(Protocol):
    async def get_snapshot(
        self, selection: PublishedSnapshotSelection
    ) -> PublishedCorpusSnapshot: ...

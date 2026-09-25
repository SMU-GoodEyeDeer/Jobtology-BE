from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import override


class RequirementNecessity(StrEnum):
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"


class SatisfactionStatus(StrEnum):
    SATISFIED = "SATISFIED"
    UNMET = "UNMET"
    NEEDS_INPUT = "NEEDS_INPUT"


class ReleaseState(StrEnum):
    PUBLISHED = "PUBLISHED"
    UNPUBLISHED = "UNPUBLISHED"
    REVOKED = "REVOKED"
    FIXTURE = "FIXTURE"


class AnalysisStatus(StrEnum):
    READY = "READY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class CoverageAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class UnavailableReason(StrEnum):
    EMPTY_BASELINE = "EMPTY_BASELINE"
    NO_REQUIREMENTS = "NO_REQUIREMENTS"


@dataclass(frozen=True, slots=True)
class EditorialBaselineError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DuplicateRequirementKeyError(EditorialBaselineError):
    requirement_key: str

    @override
    def __str__(self) -> str:
        return f"duplicate editorial requirement key: {self.requirement_key}"


@dataclass(frozen=True, slots=True)
class MissingRequirementEvidenceError(EditorialBaselineError):
    requirement_key: str

    @override
    def __str__(self) -> str:
        return f"published requirement lacks supporting evidence: {self.requirement_key}"


@dataclass(frozen=True, slots=True)
class UnusableReleaseError(EditorialBaselineError):
    release_state: ReleaseState

    @override
    def __str__(self) -> str:
        return f"editorial release cannot be used: {self.release_state}"


@dataclass(frozen=True, slots=True)
class InvalidFixtureLabelError(EditorialBaselineError):
    is_fixture: bool
    release_state: ReleaseState

    @override
    def __str__(self) -> str:
        return "fixture labeling must agree with the editorial release state"


@dataclass(frozen=True, slots=True)
class MissingPublishedReleaseIdError(EditorialBaselineError):
    @override
    def __str__(self) -> str:
        return "published editorial release requires a non-empty release ID"


@dataclass(frozen=True, slots=True)
class MissingReviewedAtError(EditorialBaselineError):
    @override
    def __str__(self) -> str:
        return "published editorial release requires a reviewed timestamp"


@dataclass(frozen=True, slots=True)
class NaiveReviewedAtError(EditorialBaselineError):
    reviewed_at: datetime

    @override
    def __str__(self) -> str:
        return "published editorial release reviewed timestamp must include a timezone"


@dataclass(frozen=True, slots=True)
class BaselineMismatchError(Exception):
    expected_occupation_id: str
    expected_basis_version: str
    actual_occupation_id: str
    actual_basis_version: str

    @override
    def __str__(self) -> str:
        return "editorial request does not match the injected baseline identity"


@dataclass(frozen=True, slots=True)
class NaiveReferenceTimeError(Exception):
    @override
    def __str__(self) -> str:
        return "editorial analysis reference time must include a timezone"


@dataclass(frozen=True, slots=True)
class EditorialReleaseMetadata:
    release_id: str | None
    state: ReleaseState
    reviewed_at: datetime | None


@dataclass(frozen=True, slots=True)
class EditorialRequirement:
    requirement_key: str
    label: str
    necessity: RequirementNecessity
    entity_id: str
    required_experience_codes: frozenset[str]
    support_refs: frozenset[str]


@dataclass(frozen=True, slots=True)
class EditorialBaseline:
    occupation_id: str
    basis_version: str
    release: EditorialReleaseMetadata
    is_fixture: bool
    requirements: tuple[EditorialRequirement, ...]

    def __post_init__(self) -> None:
        requirement_keys = tuple(requirement.requirement_key for requirement in self.requirements)
        duplicate = next(
            (
                requirement_key
                for requirement_key in requirement_keys
                if requirement_keys.count(requirement_key) > 1
            ),
            None,
        )
        if duplicate is not None:
            raise DuplicateRequirementKeyError(requirement_key=duplicate)
        match self.release.state:
            case ReleaseState.PUBLISHED:
                if self.is_fixture:
                    raise InvalidFixtureLabelError(
                        is_fixture=self.is_fixture, release_state=self.release.state
                    )
                if self.release.release_id is None or not self.release.release_id.strip():
                    raise MissingPublishedReleaseIdError()
                if self.release.reviewed_at is None:
                    raise MissingReviewedAtError()
                if (
                    self.release.reviewed_at.tzinfo is None
                    or self.release.reviewed_at.utcoffset() is None
                ):
                    raise NaiveReviewedAtError(reviewed_at=self.release.reviewed_at)
                for requirement in self.requirements:
                    if not requirement.support_refs:
                        raise MissingRequirementEvidenceError(
                            requirement_key=requirement.requirement_key
                        )
            case ReleaseState.FIXTURE:
                if not self.is_fixture:
                    raise InvalidFixtureLabelError(
                        is_fixture=self.is_fixture, release_state=self.release.state
                    )
            case ReleaseState.UNPUBLISHED | ReleaseState.REVOKED:
                raise UnusableReleaseError(release_state=self.release.state)


@dataclass(frozen=True, slots=True)
class EditorialAnalysisRequest:
    analysis_id: str
    occupation_id: str
    basis_version: str
    reference_at: datetime

    def __post_init__(self) -> None:
        if self.reference_at.tzinfo is None or self.reference_at.utcoffset() is None:
            raise NaiveReferenceTimeError()


@dataclass(frozen=True, slots=True)
class InputCompleteness:
    entities_complete: bool
    experience_complete_entity_ids: frozenset[str] = frozenset()

    def experience_is_complete_for(self, entity_id: str) -> bool:
        return entity_id in self.experience_complete_entity_ids


@dataclass(frozen=True, slots=True)
class RequirementEvaluation:
    requirement_key: str
    label: str
    necessity: RequirementNecessity
    status: SatisfactionStatus
    provenance: frozenset[str]
    support_refs: frozenset[str]


@dataclass(frozen=True, slots=True)
class Coverage:
    availability: CoverageAvailability
    reason: UnavailableReason | None
    score: float | None
    matched_count: int
    total_count: int
    score_method: str | None


@dataclass(frozen=True, slots=True)
class EditorialAnalysis:
    analysis_id: str
    profile_version: int
    occupation_id: str
    basis_version: str
    corpus_release_id: str | None
    is_fixture: bool
    reference_at: datetime
    analysis_status: AnalysisStatus
    requirements: tuple[RequirementEvaluation, ...]
    required_coverage: Coverage
    preferred_coverage: Coverage

from dataclasses import dataclass
from datetime import datetime
from typing import override


@dataclass(slots=True)
class CandidateTemplateError(Exception):
    pass


@dataclass(slots=True)
class InvalidTemplateIdError(CandidateTemplateError):
    action_id: str

    @override
    def __str__(self) -> str:
        return "activity template action ID must be non-empty"


@dataclass(slots=True)
class InvalidTemplateRevisionError(CandidateTemplateError):
    action_id: str
    revision: int

    @override
    def __str__(self) -> str:
        return f"activity template revision must be positive: {self.action_id}"


@dataclass(slots=True)
class InvalidTemplateEffortError(CandidateTemplateError):
    action_id: str
    estimated_hours: int

    @override
    def __str__(self) -> str:
        return f"activity template estimated hours must be positive: {self.action_id}"


@dataclass(slots=True)
class MissingCompletionCriteriaError(CandidateTemplateError):
    action_id: str

    @override
    def __str__(self) -> str:
        return f"activity template lacks completion criteria: {self.action_id}"


@dataclass(slots=True)
class MissingTemplateSupportError(CandidateTemplateError):
    action_id: str

    @override
    def __str__(self) -> str:
        return f"activity template lacks supporting references: {self.action_id}"


@dataclass(slots=True)
class InvalidKnownKrwCostError(CandidateTemplateError):
    krw: int

    @override
    def __str__(self) -> str:
        return "known activity cost must be nonnegative"


@dataclass(frozen=True, slots=True)
class KnownKrwCost:
    """An explicitly known out-of-pocket cost, including zero."""

    krw: int

    def __post_init__(self) -> None:
        if self.krw < 0:
            raise InvalidKnownKrwCostError(krw=self.krw)


@dataclass(frozen=True, slots=True)
class UnknownKrwCost:
    """An intentionally unavailable out-of-pocket cost."""


type KrwCost = KnownKrwCost | UnknownKrwCost


@dataclass(frozen=True, slots=True)
class ActivityTemplate:
    action_id: str
    revision: int
    title: str
    estimated_hours: int
    outcome_requirement_keys: frozenset[str]
    prerequisite_action_ids: tuple[str, ...]
    completion_criteria: tuple[str, ...]
    support_refs: frozenset[str]
    cost: KrwCost
    is_foundational: bool

    def __post_init__(self) -> None:
        if not self.action_id.strip():
            raise InvalidTemplateIdError(action_id=self.action_id)
        if self.revision <= 0:
            raise InvalidTemplateRevisionError(
                action_id=self.action_id, revision=self.revision
            )
        if self.estimated_hours <= 0:
            raise InvalidTemplateEffortError(
                action_id=self.action_id, estimated_hours=self.estimated_hours
            )
        if not self.completion_criteria:
            raise MissingCompletionCriteriaError(action_id=self.action_id)
        if not self.support_refs:
            raise MissingTemplateSupportError(action_id=self.action_id)


@dataclass(frozen=True, slots=True)
class Candidate:
    action_id: str
    template_revision: int
    title: str
    estimated_hours: int
    outcome_requirement_keys: frozenset[str]
    prerequisite_action_ids: tuple[str, ...]
    fulfilled_prerequisite_action_ids: tuple[str, ...]
    completion_criteria: tuple[str, ...]
    support_refs: frozenset[str]
    cost: KrwCost


@dataclass(frozen=True, slots=True)
class InsufficientAnalysisBlocker:
    analysis_id: str


@dataclass(frozen=True, slots=True)
class NeedsInputBlocker:
    requirement_key: str
    label: str
    support_refs: frozenset[str]


@dataclass(frozen=True, slots=True)
class NoActivitiesBlocker:
    requirement_key: str
    label: str
    support_refs: frozenset[str]


type CandidateBlocker = (
    InsufficientAnalysisBlocker | NeedsInputBlocker | NoActivitiesBlocker
)


@dataclass(frozen=True, slots=True)
class CandidateSet:
    """Candidate DAG and unresolved planning inputs for one editorial analysis."""

    analysis_id: str
    profile_version: int
    occupation_id: str
    basis_version: str
    corpus_release_id: str | None
    is_fixture: bool
    reference_at: datetime
    candidates: tuple[Candidate, ...]
    blockers: tuple[CandidateBlocker, ...]

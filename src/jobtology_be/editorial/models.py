"""Draft-only file boundary; these types cannot be used as published corpus objects."""

from hashlib import sha256
from typing import Annotated, ClassVar, Literal, Self, assert_never

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    model_validator,
)

from jobtology_be.planning.candidate_models import (
    ActivityTemplate,
    CandidateTemplateError,
    KnownKrwCost,
    UnknownKrwCost,
)
from jobtology_be.planning.candidates import (
    AmbiguousTemplateRevisionError,
    CandidateGenerator,
    DuplicateTemplateRevisionError,
    MissingPrerequisiteTemplateError,
    PrerequisiteCycleError,
)

Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
ROLES = frozenset({"AI_ENGINEER", "BACKEND_DEVELOPER", "FRONTEND_DEVELOPER", "DATA_ANALYST"})


class DraftValidationError(ValueError):
    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class DraftRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True
    )


class DraftRequirement(DraftRecord):
    requirement_key: Text
    label: Text
    necessity: Literal["REQUIRED", "RECOMMENDED"]
    suggested_learning_outcome: Text
    source_refs: tuple[Text, ...] = ()


class UnknownCost(DraftRecord):
    kind: Literal["UNKNOWN"]


class KnownCost(DraftRecord):
    kind: Literal["KNOWN_KRW"]
    krw: int = Field(ge=0)


class DraftActivity(DraftRecord):
    action_id: Text
    revision: int = Field(ge=1)
    title: Text
    activity_type: Literal["PROJECT", "STUDY"]
    estimated_hours: int = Field(gt=0)
    outcome_requirement_keys: tuple[Text, ...] = Field(min_length=1)
    prerequisite_action_ids: tuple[Text, ...]
    completion_criteria: tuple[Text, ...] = Field(min_length=1)
    source_refs: tuple[Text, ...] = ()
    cost: Annotated[UnknownCost | KnownCost, Field(discriminator="kind")]
    is_foundational: bool

    def _validation_template(self) -> ActivityTemplate:
        # Structural validation only: this marker is never exported as evidence or a candidate.
        match self.cost:
            case KnownCost(krw=krw):
                cost = KnownKrwCost(krw)
            case UnknownCost():
                cost = UnknownKrwCost()
            case unreachable:
                assert_never(unreachable)
        return ActivityTemplate(
            action_id=self.action_id,
            revision=self.revision,
            title=self.title,
            estimated_hours=self.estimated_hours,
            outcome_requirement_keys=frozenset(self.outcome_requirement_keys),
            prerequisite_action_ids=self.prerequisite_action_ids,
            completion_criteria=self.completion_criteria,
            support_refs=frozenset({"DRAFT_VALIDATION_ONLY"}),
            cost=cost,
            is_foundational=self.is_foundational,
        )


class DraftOccupation(DraftRecord):
    occupation_id: Text
    title: Text
    status: Literal["DRAFT"]
    authored_by: Literal["ASSISTANT"]
    reviewed_at: None
    requirements: tuple[DraftRequirement, ...] = Field(min_length=1)
    activities: tuple[DraftActivity, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        keys = [item.requirement_key for item in self.requirements]
        if len(keys) != len(set(keys)):
            raise DraftValidationError("duplicate requirement key")
        actions = [item.action_id for item in self.activities]
        if len(actions) != len(set(actions)):
            raise DraftValidationError("duplicate activity action ID")
        known = set(keys)
        for activity in self.activities:
            if missing := set(activity.outcome_requirement_keys) - known:
                raise DraftValidationError(f"unknown outcome requirement: {min(missing)}")
            if len(activity.outcome_requirement_keys) != len(
                set(activity.outcome_requirement_keys)
            ):
                raise DraftValidationError("duplicate outcome requirement key")
            if len(activity.prerequisite_action_ids) != len(set(activity.prerequisite_action_ids)):
                raise DraftValidationError("duplicate prerequisite action ID")
        try:
            _ = CandidateGenerator(tuple(item._validation_template() for item in self.activities))
        except (
            CandidateTemplateError,
            AmbiguousTemplateRevisionError,
            DuplicateTemplateRevisionError,
            MissingPrerequisiteTemplateError,
            PrerequisiteCycleError,
        ) as error:
            raise DraftValidationError(f"invalid prerequisite graph: {error}") from error
        return self


class DraftCatalog(DraftRecord):
    version: Literal[1]
    occupations: tuple[DraftOccupation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_roles(self) -> Self:
        ids = [item.occupation_id for item in self.occupations]
        if len(ids) != len(set(ids)):
            raise DraftValidationError("duplicate occupation ID")
        if frozenset(ids) != ROLES:
            raise DraftValidationError("four required occupation IDs must be present")
        actions = [item.action_id for role in self.occupations for item in role.activities]
        if len(actions) != len(set(actions)):
            raise DraftValidationError("duplicate activity action ID across occupations")
        return self

    @computed_field
    @property
    def content_sha256(self) -> str:
        return sha256(self.model_dump_json(exclude={"content_sha256"}).encode("utf-8")).hexdigest()

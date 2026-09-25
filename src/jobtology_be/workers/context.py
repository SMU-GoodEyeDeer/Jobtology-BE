from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Protocol, override
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from jobtology_be.contracts import CapabilityInput
from jobtology_be.corpus.snapshot import PublishedSnapshotSelection
from jobtology_be.infrastructure.persistence.contracts import JsonValue, RecomputeWorkItem
from jobtology_be.modules.analyses.editorial_models import InputCompleteness
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.planning.solver_models import (
    CandidateAvailability,
    PlanningSlot,
    SolverSettings,
)
from jobtology_be.settings import CorpusSource


@dataclass(frozen=True, slots=True)
class InvalidRecomputeContextError(Exception):
    @override
    def __str__(self) -> str:
        return "persisted recompute context is invalid"


@dataclass(frozen=True, slots=True)
class RecomputeContext:
    user_id: UUID
    profile_version: int
    goal_id: UUID
    snapshot_selection: PublishedSnapshotSelection
    capabilities: tuple[CapabilityInput, ...]
    completeness: InputCompleteness
    constraints: PlanningConstraints
    reference_at: datetime
    planning_started_at: datetime
    calendar: tuple[PlanningSlot, ...]
    candidate_availability: tuple[CandidateAvailability, ...] = ()
    solver_settings: SolverSettings = field(default_factory=SolverSettings)
    corpus_source: CorpusSource = "local_json"


class _ContextJson(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class _SnapshotSelectionJson(_ContextJson):
    occupation_id: str
    basis_version: str
    release_id: str
    source: CorpusSource = "local_json"


class _CapabilityJson(_ContextJson):
    raw_text: str
    entity_id: str | None = None
    experience_codes: tuple[str, ...] = ()


class _CompletenessJson(_ContextJson):
    entities_complete: bool
    experience_complete_entity_ids: frozenset[str] = frozenset()


class _PlanningHourJson(_ContextJson):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    capacity_week_key: str


class _CandidateAvailabilityJson(_ContextJson):
    action_id: str
    available_from: AwareDatetime
    available_until: AwareDatetime


class _SolverSettingsJson(_ContextJson):
    time_limit_seconds: float = Field(default=20.0, ge=0)


class RecomputeContextDocument(_ContextJson):
    user_id: UUID
    profile_version: int = Field(ge=1)
    goal_id: UUID
    snapshot_selection: _SnapshotSelectionJson
    capabilities: tuple[_CapabilityJson, ...]
    completeness: _CompletenessJson
    constraints: PlanningConstraints
    reference_at: AwareDatetime
    planning_started_at: AwareDatetime
    calendar: tuple[_PlanningHourJson, ...]
    candidate_availability: tuple[_CandidateAvailabilityJson, ...] = ()
    solver_settings: _SolverSettingsJson = Field(default_factory=_SolverSettingsJson)

    def to_context(self) -> RecomputeContext:
        return RecomputeContext(
            user_id=self.user_id,
            profile_version=self.profile_version,
            goal_id=self.goal_id,
            snapshot_selection=PublishedSnapshotSelection(
                occupation_id=self.snapshot_selection.occupation_id,
                basis_version=self.snapshot_selection.basis_version,
                release_id=self.snapshot_selection.release_id,
            ),
            capabilities=tuple(
                CapabilityInput(
                    raw_text=capability.raw_text,
                    entity_id=capability.entity_id,
                    experience_codes=list(capability.experience_codes),
                )
                for capability in self.capabilities
            ),
            completeness=InputCompleteness(
                entities_complete=self.completeness.entities_complete,
                experience_complete_entity_ids=self.completeness.experience_complete_entity_ids,
            ),
            constraints=PlanningConstraints.model_validate(self.constraints.model_dump()),
            reference_at=self.reference_at,
            planning_started_at=self.planning_started_at,
            calendar=tuple(
                PlanningSlot(
                    starts_at=hour.starts_at,
                    ends_at=hour.ends_at,
                    capacity_week_key=hour.capacity_week_key,
                )
                for hour in self.calendar
            ),
            candidate_availability=tuple(
                CandidateAvailability(
                    action_id=availability.action_id,
                    available_from=availability.available_from,
                    available_until=availability.available_until,
                )
                for availability in self.candidate_availability
            ),
            solver_settings=SolverSettings(
                time_limit_seconds=self.solver_settings.time_limit_seconds
            ),
            corpus_source=self.snapshot_selection.source,
        )


class RecomputeContextPayloadReader(Protocol):
    async def load_recompute_context(
        self, work_item: RecomputeWorkItem
    ) -> Mapping[str, JsonValue]: ...


@dataclass(frozen=True, slots=True)
class JsonRecomputeContextReader:
    payload_reader: RecomputeContextPayloadReader

    async def get_context(self, work_item: RecomputeWorkItem) -> RecomputeContext:
        payload = await self.payload_reader.load_recompute_context(work_item)
        try:
            return RecomputeContextDocument.model_validate(payload).to_context()
        except ValidationError as error:
            raise InvalidRecomputeContextError() from error

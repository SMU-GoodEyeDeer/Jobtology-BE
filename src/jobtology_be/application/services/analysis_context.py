from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from jobtology_be.application.services.analyses import AnalysisRequestCommand
from jobtology_be.contracts import CapabilityInput
from jobtology_be.corpus.snapshot import (
    PublishedCorpusSnapshotReader,
    PublishedSnapshotSelection,
)
from jobtology_be.modules.analyses.editorial_models import InputCompleteness
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.planning.solver_models import (
    CandidateAvailability,
    PlanningSlot,
    SolverSettings,
)
from jobtology_be.workers.context import RecomputeContextDocument


@dataclass(frozen=True, slots=True)
class ContextSnapshotConfiguration:
    basis_version: str
    release_id: str
    allow_fixture: bool = False


@dataclass(frozen=True, slots=True)
class AnalysisContextInputs:
    occupation_id: str
    capabilities: tuple[CapabilityInput, ...]
    completeness: InputCompleteness
    constraints: PlanningConstraints
    calendar: tuple[PlanningSlot, ...]
    candidate_availability: tuple[CandidateAvailability, ...]
    solver_settings: SolverSettings


class AnalysisContextInputSource(Protocol):
    async def load_context_inputs(
        self,
        user_id: UUID,
        command: AnalysisRequestCommand,
        reference_at: datetime,
    ) -> AnalysisContextInputs: ...


def _current_utc() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SnapshotBackedAnalysisContextFactory:
    source: AnalysisContextInputSource
    snapshot_reader: PublishedCorpusSnapshotReader
    configuration: ContextSnapshotConfiguration
    now: Callable[[], datetime] = _current_utc

    async def create_context(
        self, user_id: UUID, command: AnalysisRequestCommand
    ) -> RecomputeContextDocument:
        reference_at = self.now()
        inputs = await self.source.load_context_inputs(user_id, command, reference_at)
        selection = PublishedSnapshotSelection(
            occupation_id=inputs.occupation_id,
            basis_version=self.configuration.basis_version,
            release_id=self.configuration.release_id,
        )
        snapshot = await self.snapshot_reader.get_snapshot(selection)
        _ = snapshot.baseline_for_recompute(allow_fixture=self.configuration.allow_fixture)
        document = RecomputeContextDocument.model_validate(
            {
                "user_id": user_id,
                "profile_version": command.expected_profile_version,
                "goal_id": command.goal_id,
                "snapshot_selection": {
                    "occupation_id": selection.occupation_id,
                    "basis_version": selection.basis_version,
                    "release_id": selection.release_id,
                    "source": "local_json",
                },
                "capabilities": tuple(
                    {
                        "raw_text": capability.raw_text,
                        "entity_id": capability.entity_id,
                        "experience_codes": tuple(capability.experience_codes),
                    }
                    for capability in inputs.capabilities
                ),
                "completeness": {
                    "entities_complete": inputs.completeness.entities_complete,
                    "experience_complete_entity_ids": inputs.completeness.experience_complete_entity_ids,
                },
                "constraints": inputs.constraints,
                "reference_at": reference_at,
                "planning_started_at": reference_at,
                "calendar": tuple(
                    {
                        "starts_at": slot.starts_at,
                        "ends_at": slot.ends_at,
                        "capacity_week_key": slot.capacity_week_key,
                    }
                    for slot in inputs.calendar
                ),
                "candidate_availability": tuple(
                    {
                        "action_id": availability.action_id,
                        "available_from": availability.available_from,
                        "available_until": availability.available_until,
                    }
                    for availability in inputs.candidate_availability
                ),
                "solver_settings": {"time_limit_seconds": inputs.solver_settings.time_limit_seconds},
            }
        )
        _ = document.to_context()
        return document

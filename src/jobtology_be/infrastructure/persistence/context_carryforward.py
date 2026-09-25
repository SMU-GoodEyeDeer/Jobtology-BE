from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import override
from uuid import UUID

from pydantic import ValidationError

from jobtology_be.contracts import CapabilityInput
from jobtology_be.infrastructure.persistence.contracts import JsonValue
from jobtology_be.modules.analyses.editorial_models import InputCompleteness
from jobtology_be.planning.calendar import FlexibleCalendarError, flexible_weekly_slots
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.workers.context import RecomputeContextDocument


@dataclass(frozen=True, slots=True)
class ContextCarryforwardError(Exception):
    @override
    def __str__(self) -> str:
        return "cannot rebuild a current recompute context"


@dataclass(frozen=True, slots=True)
class ContextCarryforwardInput:
    user_id: UUID
    goal_id: UUID
    goal_occupation_id: str
    profile_version: int
    goal_target_by: datetime
    capabilities: tuple[CapabilityInput, ...]
    completeness: InputCompleteness
    now: datetime
    current_constraints: PlanningConstraints | None = None


def carry_forward_recompute_context(
    previous_payload: Mapping[str, JsonValue], input: ContextCarryforwardInput
) -> Mapping[str, JsonValue]:
    try:
        previous = RecomputeContextDocument.model_validate(previous_payload)
        if previous.user_id != input.user_id or previous.goal_id != input.goal_id:
            raise ContextCarryforwardError()
        if previous.snapshot_selection.occupation_id != input.goal_occupation_id:
            raise ContextCarryforwardError()
        constraints = input.current_constraints or previous.constraints.model_copy(
            update={"target_by": input.goal_target_by}
        )
        if constraints.target_by != input.goal_target_by:
            raise ContextCarryforwardError()
        calendar = tuple(
            {
                "starts_at": slot.starts_at.isoformat(),
                "ends_at": slot.ends_at.isoformat(),
                "capacity_week_key": slot.capacity_week_key,
            }
            for slot in flexible_weekly_slots(
                planning_started_at=input.now,
                target_by=constraints.target_by,
                available_hours_per_week=constraints.available_hours_per_week,
            )
        )
        if not calendar:
            raise ContextCarryforwardError()
        payload: Mapping[str, JsonValue] = {
            "user_id": str(input.user_id),
            "profile_version": input.profile_version,
            "goal_id": str(input.goal_id),
            "snapshot_selection": previous.snapshot_selection.model_dump(mode="json"),
            "capabilities": tuple(
                capability.model_dump(mode="json") for capability in input.capabilities
            ),
            "completeness": {
                "entities_complete": input.completeness.entities_complete,
                "experience_complete_entity_ids": sorted(
                    input.completeness.experience_complete_entity_ids
                ),
            },
            "constraints": constraints.model_dump(mode="json"),
            "reference_at": input.now.isoformat(),
            "planning_started_at": input.now.isoformat(),
            "calendar": calendar,
            "candidate_availability": tuple(
                {
                    "action_id": availability.action_id,
                    "available_from": max(availability.available_from, input.now).isoformat(),
                    "available_until": availability.available_until.isoformat(),
                }
                for availability in previous.candidate_availability
                if availability.available_until >= input.now
            ),
            "solver_settings": previous.solver_settings.model_dump(mode="json"),
        }
        return RecomputeContextDocument.model_validate(payload).model_dump(mode="json")
    except (FlexibleCalendarError, ValidationError) as error:
        raise ContextCarryforwardError() from error

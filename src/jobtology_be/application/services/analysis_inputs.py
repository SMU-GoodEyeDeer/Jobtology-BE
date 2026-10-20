from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select

from jobtology_be.application.services.analyses import AnalysisRequestCommand
from jobtology_be.application.services.analysis_context import AnalysisContextInputs
from jobtology_be.application.services.preferences import DEFAULT_ROUTE_PREFERENCES
from jobtology_be.application.specs import capability_inputs
from jobtology_be.infrastructure.persistence.contracts import (
    MissingRecordError,
    PersistenceConflictError,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    goals,
    profiles,
    route_preferences,
    user_capabilities,
)
from jobtology_be.modules.analyses.editorial_models import InputCompleteness
from jobtology_be.planning.calendar import FlexibleCalendarError, flexible_weekly_slots
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.planning.solver_models import SolverSettings


class _CapabilityDetails(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    experience_codes: tuple[str, ...] = ()


class AnalysisContextInputsUnavailableError(Exception):
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ActiveTargetedGoalRequiredError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PostgresAnalysisContextInputSource:
    database: Database
    capability_list_authoritative: bool = False

    async def load_context_inputs(
        self,
        user_id: UUID,
        command: AnalysisRequestCommand,
        reference_at: datetime,
    ) -> AnalysisContextInputs:
        if not _is_aware(reference_at):
            raise AnalysisContextInputsUnavailableError("reference time must be timezone-aware")

        async with self.database.sessions.begin() as session:
            profile_version = await session.scalar(
                select(profiles.c.version)
                .where(profiles.c.user_id == user_id)
                .with_for_update()
            )
            if profile_version != command.expected_profile_version:
                raise PersistenceConflictError(resource="profile")

            goal = await session.execute(
                select(
                    goals.c.goal_mode, goals.c.status, goals.c.occupation_id,
                    goals.c.target_by, goals.c.timezone,
                ).where(
                    goals.c.id == command.goal_id,
                    goals.c.user_id == user_id,
                )
            )
            goal_row = goal.one_or_none()
            if goal_row is None:
                raise MissingRecordError(resource="goal")
            if (
                goal_row.status != "ACTIVE" or goal_row.goal_mode != "TARGETED"
                or not goal_row.occupation_id
            ):
                raise ActiveTargetedGoalRequiredError()

            preference = await session.execute(
                select(
                    route_preferences.c.available_hours_per_week,
                    route_preferences.c.budget_mode,
                    route_preferences.c.max_out_of_pocket_krw,
                    route_preferences.c.fastest_path,
                    route_preferences.c.needs_portfolio,
                    route_preferences.c.career_switch,
                    route_preferences.c.weekly_availability,
                    route_preferences.c.unavailable_dates,
                ).where(route_preferences.c.user_id == user_id)
            )
            preference_row = preference.one_or_none()

            capability_rows = (
                await session.execute(
                    select(
                        user_capabilities.c.raw_text,
                        user_capabilities.c.entity_id,
                        user_capabilities.c.details,
                        user_capabilities.c.category,
                        user_capabilities.c.proficiency,
                        user_capabilities.c.source_completion_event_id,
                    ).where(
                        user_capabilities.c.user_id == user_id,
                        user_capabilities.c.lifecycle == "ACTIVE",
                    )
                )
            ).all()

        try:
            timezone = ZoneInfo(goal_row.timezone)
            target_by = goal_row.target_by.astimezone(timezone)
            local_reference_at = reference_at.astimezone(timezone)
            details = tuple(
                _CapabilityDetails.model_validate(capability.details)
                for capability in capability_rows
            )
            constraints = PlanningConstraints.model_validate(
                {"target_by": target_by, **DEFAULT_ROUTE_PREFERENCES}
                if preference_row is None
                else {
                    "target_by": target_by,
                    "available_hours_per_week": preference_row.available_hours_per_week,
                    "budget_mode": preference_row.budget_mode,
                    "max_out_of_pocket_krw": preference_row.max_out_of_pocket_krw,
                    "fastest_path": preference_row.fastest_path,
                    "needs_portfolio": preference_row.needs_portfolio,
                    "career_switch": preference_row.career_switch,
                    "weekly_availability": preference_row.weekly_availability,
                    "unavailable_dates": preference_row.unavailable_dates,
                }
            )
            calendar = flexible_weekly_slots(
                planning_started_at=local_reference_at,
                target_by=target_by,
                available_hours_per_week=constraints.available_hours_per_week,
                weekly_availability=constraints.weekly_availability,
                unavailable_dates=constraints.unavailable_dates,
            )
        except (FlexibleCalendarError, ValidationError, ValueError, ZoneInfoNotFoundError) as error:
            raise AnalysisContextInputsUnavailableError(str(error)) from error

        if not calendar:
            raise AnalysisContextInputsUnavailableError("no flexible calendar slots are available")

        capabilities = tuple(
            item
            for capability in capability_rows
            for item in capability_inputs(
                raw_text=capability.raw_text,
                entity_id=capability.entity_id,
                category=capability.category, proficiency=capability.proficiency,
                details=capability.details,
                completed=capability.source_completion_event_id is not None,
                today=local_reference_at.date(),
            )
        )
        experience_complete_entity_ids = frozenset(
            capability.entity_id
            for capability, capability_details in zip(capability_rows, details, strict=True)
            if capability.entity_id is not None
            and "experience_codes" in capability_details.model_fields_set
        )
        return AnalysisContextInputs(
            occupation_id=goal_row.occupation_id,
            capabilities=capabilities,
            completeness=InputCompleteness(
                entities_complete=self.capability_list_authoritative or (
                    bool(capabilities)
                    and all(capability.entity_id is not None for capability in capabilities)
                ),
                experience_complete_entity_ids=experience_complete_entity_ids,
            ),
            constraints=constraints,
            calendar=calendar,
            candidate_availability=(),
            solver_settings=SolverSettings(),
            calendar_policy="FLEXIBLE_WEEKLY_V1",
        )


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

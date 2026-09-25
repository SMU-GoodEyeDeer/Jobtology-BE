from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import anyio
import pytest
from sqlalchemy import insert

from jobtology_be.application.services.analyses import AnalysisRequestCommand
from jobtology_be.application.services.analysis_inputs import (
    AnalysisContextInputsUnavailableError,
    PostgresAnalysisContextInputSource,
)
from jobtology_be.infrastructure.persistence.contracts import PersistenceConflictError, UserCreate
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    goals,
    route_preferences,
    user_capabilities,
)
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore

pytest_plugins = ("test_acceptance_m5_lifecycle",)

REFERENCE_AT = datetime(2026, 9, 22, 12, 30, tzinfo=UTC)


def test_analysis_context_inputs_unavailable_error_can_propagate_with_a_traceback() -> None:
    with pytest.raises(AnalysisContextInputsUnavailableError) as captured:
        raise AnalysisContextInputsUnavailableError("route preferences are unavailable")

    assert captured.value.reason == "route preferences are unavailable"
    assert captured.value.__traceback__ is not None


async def _seed_context_inputs(
    database: Database,
    *,
    timezone: str = "UTC",
    include_preferences: bool = True,
    target_by: datetime | None = None,
) -> tuple[UUID, UUID]:
    user_id = uuid4()
    goal_id = uuid4()
    store = PostgresApplicationStore(database)
    await store.create_user(UserCreate(user_id=user_id))
    async with database.sessions.begin() as session:
        await session.execute(
            insert(goals).values(
                id=goal_id,
                user_id=user_id,
                goal_mode="TARGETED",
                occupation_id="BACKEND_DEVELOPER",
                target_by=target_by or REFERENCE_AT + timedelta(days=30),
                timezone=timezone,
                original_time_phrase="next month",
                status="ACTIVE",
            )
        )
        await session.execute(
            insert(user_capabilities).values(
                id=uuid4(),
                user_id=user_id,
                category="SKILL",
                raw_text="Python",
                entity_id="SKILL_PYTHON",
                details={"experience_codes": ["PROFESSIONAL"]},
            )
        )
        if include_preferences:
            await session.execute(
                insert(route_preferences).values(
                    user_id=user_id,
                    available_hours_per_week=4,
                    availability_source="SELF_REPORTED",
                    budget_mode="REGULAR",
                    max_out_of_pocket_krw=None,
                    fastest_path=False,
                    needs_portfolio=False,
                    career_switch=False,
                )
            )
    return user_id, goal_id


async def _exercise_source(database_url: str) -> None:
    database = Database.create(database_url)
    source = PostgresAnalysisContextInputSource(database=database)
    try:
        user_id, goal_id = await _seed_context_inputs(database)
        command = AnalysisRequestCommand(
            goal_id=goal_id,
            expected_profile_version=1,
            basis_type="EDITORIAL",
        )

        inputs = await source.load_context_inputs(user_id, command, REFERENCE_AT)

        assert inputs.occupation_id == "BACKEND_DEVELOPER"
        assert inputs.constraints.available_hours_per_week == 4
        assert len(inputs.calendar) == 20
        assert inputs.calendar[0].starts_at == datetime(2026, 9, 22, 13, tzinfo=UTC)
        assert {slot.capacity_week_key for slot in inputs.calendar} == {
            "2026-W39",
            "2026-W40",
            "2026-W41",
            "2026-W42",
            "2026-W43",
        }
        assert inputs.completeness.entities_complete is True
        assert inputs.completeness.experience_complete_entity_ids == frozenset({"SKILL_PYTHON"})

        with pytest.raises(PersistenceConflictError):
            await source.load_context_inputs(
                user_id,
                AnalysisRequestCommand(
                    goal_id=goal_id,
                    expected_profile_version=2,
                    basis_type="EDITORIAL",
                ),
                REFERENCE_AT,
            )

        missing_preferences_user_id, missing_preferences_goal_id = await _seed_context_inputs(
            database,
            include_preferences=False,
        )
        with pytest.raises(AnalysisContextInputsUnavailableError, match="route preferences"):
            await source.load_context_inputs(
                missing_preferences_user_id,
                AnalysisRequestCommand(
                    goal_id=missing_preferences_goal_id,
                    expected_profile_version=1,
                    basis_type="EDITORIAL",
                ),
                REFERENCE_AT,
            )

        invalid_timezone_user_id, invalid_timezone_goal_id = await _seed_context_inputs(
            database,
            timezone="Invalid/Timezone",
        )
        with pytest.raises(AnalysisContextInputsUnavailableError):
            await source.load_context_inputs(
                invalid_timezone_user_id,
                AnalysisRequestCommand(
                    goal_id=invalid_timezone_goal_id,
                    expected_profile_version=1,
                    basis_type="EDITORIAL",
                ),
                REFERENCE_AT,
            )

        long_horizon_user_id, long_horizon_goal_id = await _seed_context_inputs(
            database,
            target_by=REFERENCE_AT + timedelta(days=367),
        )
        with pytest.raises(AnalysisContextInputsUnavailableError, match="horizon"):
            await source.load_context_inputs(
                long_horizon_user_id,
                AnalysisRequestCommand(
                    goal_id=long_horizon_goal_id,
                    expected_profile_version=1,
                    basis_type="EDITORIAL",
                ),
                REFERENCE_AT,
            )
    finally:
        await database.dispose()


def test_postgres_analysis_context_source_reads_version_fenced_explicit_inputs(
    acceptance_database_url: str,
) -> None:
    anyio.run(_exercise_source, acceptance_database_url)

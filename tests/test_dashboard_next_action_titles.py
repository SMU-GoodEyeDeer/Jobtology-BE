from datetime import UTC, datetime
from unittest.mock import MagicMock, create_autospec
from uuid import UUID, uuid4

import anyio
from sqlalchemy import Select, Table
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.application.m5_queries import DashboardRoadmapView
from jobtology_be.infrastructure.persistence.m5_dashboard_actions import load_next_actions
from jobtology_be.infrastructure.persistence.m5_dashboard_models import ActiveRoadmapRow

REFERENCE_AT = datetime(2026, 10, 11, 9, tzinfo=UTC)


def _session(rows_by_table: dict[str, object]) -> AsyncSession:
    """Answer each SELECT by the table it reads, independent of query order."""

    def rows(statement: Select[tuple[object, ...]]) -> object:
        (table,) = statement.get_final_froms()
        assert isinstance(table, Table)
        return rows_by_table[table.name]

    def execute(statement: Select[tuple[object, ...]]) -> MagicMock:
        result = MagicMock()
        result.mappings.return_value.all.return_value = rows(statement)
        return result

    session = create_autospec(AsyncSession, instance=True)
    session.execute.side_effect = execute
    session.scalar.side_effect = rows
    return session


def _step(step_key: str, position: int, action_id: str) -> dict[str, object]:
    return {
        "id": uuid4(),
        "step_key": step_key,
        "position": position,
        "action_id": action_id,
        "state": "TODO",
        "planned_end": None,
    }


def _roadmap(proposal_id: UUID) -> ActiveRoadmapRow:
    return ActiveRoadmapRow(
        view=DashboardRoadmapView(
            roadmap_id=uuid4(),
            roadmap_version=1,
            proposal_id=proposal_id,
            profile_version=3,
            release_id=None,
            title="Roadmap",
        ),
        validity={},
    )


def test_next_actions_use_proposal_step_titles_and_fall_back_to_action_id() -> None:
    # Given a roadmap whose proposal titles only its first step
    titled = _step("step-1", 0, "PORTFOLIO_PROJECT")
    untitled = _step("step-2", 1, "SQL_PRACTICE")
    session = _session(
        {
            "roadmap_steps": [titled, untitled],
            "step_dependencies": [],
            "route_proposals": [{"step_key": "step-1", "title": "포트폴리오 프로젝트"}],
        }
    )

    # When
    actions = anyio.run(load_next_actions, session, _roadmap(uuid4()), REFERENCE_AT)

    # Then
    assert {action.step_id: action.title for action in actions} == {
        titled["id"]: "포트폴리오 프로젝트",
        untitled["id"]: "SQL_PRACTICE",
    }

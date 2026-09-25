from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.queries import (
    CapabilityView,
    GoalView,
    RoadmapDiffView,
    RoadmapScheduleChangeView,
)
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

USER_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
GOAL_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b5")
CAPABILITY_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b6")
PROPOSAL_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b7")
ROADMAP_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b8")
UPDATED_AT = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


class StaticIdentityProvider:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=USER_ID)


class StaticProductQueries:
    async def list_goals(self, user_id: UUID) -> tuple[GoalView, ...]:
        return (
            GoalView(
                goal_id=GOAL_ID,
                goal_mode="TARGETED",
                occupation_id="occupation:backend-engineer",
                target_by=UPDATED_AT,
                timezone="UTC",
                original_time_phrase="by next year",
                status="ACTIVE",
                created_at=UPDATED_AT,
                updated_at=UPDATED_AT,
            ),
        )

    async def get_goal(self, user_id: UUID, goal_id: UUID) -> GoalView:
        return (await self.list_goals(user_id))[0]

    async def list_capabilities(self, user_id: UUID) -> tuple[CapabilityView, ...]:
        return (
            CapabilityView(
                capability_id=CAPABILITY_ID,
                category="SKILL",
                raw_text="FastAPI",
                entity_id="capability:fastapi",
                proficiency="INTERMEDIATE",
                verification="SELF_REPORTED",
                lifecycle="ACTIVE",
                details={"source": "self"},
                source_completion_event_id=None,
                created_at=UPDATED_AT,
                updated_at=UPDATED_AT,
            ),
        )

    async def get_capability(self, user_id: UUID, capability_id: UUID) -> CapabilityView:
        return (await self.list_capabilities(user_id))[0]

    async def get_roadmap_diff(
        self, user_id: UUID, roadmap_id: UUID, proposal_id: UUID
    ) -> RoadmapDiffView:
        return RoadmapDiffView(
            roadmap_id=ROADMAP_ID,
            roadmap_version=3,
            proposal_id=PROPOSAL_ID,
            proposal_hash="proposal-hash",
            retained_step_keys=("project",),
            added_step_keys=("portfolio",),
            removed_step_keys=("algorithm",),
            reordered_step_keys=("project",),
            schedule_changes=(
                RoadmapScheduleChangeView(
                    step_key="project",
                    old_planned_start=UPDATED_AT,
                    old_planned_end=UPDATED_AT,
                    new_planned_start=UPDATED_AT,
                    new_planned_end=UPDATED_AT,
                ),
            ),
            constraint_changes={"available_hours_per_week": (10, 15)},
        )


def test_goal_and_capability_reads_use_the_authenticated_query_scope() -> None:
    # Given
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            product_queries=StaticProductQueries(),
        ),
    )

    # When
    with TestClient(app) as client:
        goals_response = client.get("/api/v1/me/goals")
        goal_response = client.get(f"/api/v1/me/goals/{GOAL_ID}")
        capabilities_response = client.get("/api/v1/me/capabilities")
        capability_response = client.get(f"/api/v1/me/capabilities/{CAPABILITY_ID}")
        diff_response = client.get(
            f"/api/v1/roadmaps/{ROADMAP_ID}/diff",
            params={"proposal_id": str(PROPOSAL_ID)},
        )

    # Then
    assert goals_response.status_code == 200
    assert goal_response.status_code == 200
    assert capabilities_response.status_code == 200
    assert capability_response.status_code == 200
    assert diff_response.status_code == 200
    assert goals_response.json()["items"][0]["goal_id"] == str(GOAL_ID)
    assert goal_response.json()["status"] == "ACTIVE"
    assert capabilities_response.json()["items"][0]["capability_id"] == str(CAPABILITY_ID)
    assert capability_response.json()["lifecycle"] == "ACTIVE"
    assert diff_response.json()["added_step_keys"] == ["portfolio"]

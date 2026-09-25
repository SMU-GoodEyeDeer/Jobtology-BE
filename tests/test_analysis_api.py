from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import SecretStr

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.services.analyses import AnalysisRequestCommand, AnalysisRequestResult
from jobtology_be.infrastructure.persistence.contracts import (
    AnalysisRecomputeSubmission,
    IdempotencyAcquire,
    IdempotencyComplete,
    IdempotencySnapshot,
    JsonValue,
    RecomputeRequestSnapshot,
)
from jobtology_be.main import create_app
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.settings import Settings
from jobtology_be.workers.context import RecomputeContextDocument

USER_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
GOAL_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b5")
RECOMPUTE_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b6")
REFERENCE_AT = datetime(2026, 9, 22, 12, tzinfo=UTC)


class StaticIdentityProvider:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=USER_ID)


class StaticAnalysisService:
    command: AnalysisRequestCommand | None = None

    async def request(self, user_id: UUID, command: AnalysisRequestCommand) -> AnalysisRequestResult:
        self.command = command
        return AnalysisRequestResult(recompute_request_id=RECOMPUTE_ID, state="PENDING")


def _context_document() -> RecomputeContextDocument:
    return RecomputeContextDocument.model_validate(
        {
            "user_id": USER_ID,
            "profile_version": 3,
            "goal_id": GOAL_ID,
            "snapshot_selection": {
                "occupation_id": "BACKEND_DEVELOPER",
                "basis_version": "reviewed-v1",
                "release_id": "release-reviewed-v1",
            },
            "capabilities": (),
            "completeness": {"entities_complete": True},
            "constraints": PlanningConstraints(
                target_by=datetime(2026, 10, 22, 12, tzinfo=UTC),
                available_hours_per_week=4,
            ),
            "reference_at": REFERENCE_AT,
            "planning_started_at": REFERENCE_AT,
            "calendar": (),
        }
    )


@dataclass(frozen=True, slots=True)
class StaticAnalysisContextFactory:
    context: RecomputeContextDocument

    async def create_context(
        self, user_id: UUID, command: AnalysisRequestCommand
    ) -> RecomputeContextDocument:
        assert user_id == USER_ID
        assert command.goal_id == GOAL_ID
        return self.context


@dataclass(slots=True)
class RecordingAnalysisSubmitter:
    submission: AnalysisRecomputeSubmission | None = None

    async def submit_analysis_recompute(
        self, submission: AnalysisRecomputeSubmission
    ) -> RecomputeRequestSnapshot:
        self.submission = submission
        return RecomputeRequestSnapshot(request_id=RECOMPUTE_ID, state="PENDING")


class ReplayIdempotencyStore:
    async def acquire_idempotency(self, request: IdempotencyAcquire) -> IdempotencySnapshot:
        return IdempotencySnapshot(
            response_status=202,
            response={
                "recompute_request_id": str(RECOMPUTE_ID),
                "state": "PENDING",
                "status_url": f"/api/v1/recomputations/{RECOMPUTE_ID}",
            },
        )

    async def complete_idempotency(self, request: IdempotencyComplete) -> None:
        raise AssertionError("replayed requests must not be completed again")

    async def execute_idempotency(
        self,
        request: IdempotencyAcquire,
        response_status: int,
        operation: Callable[[], Awaitable[Mapping[str, JsonValue]]],
    ) -> IdempotencySnapshot:
        return IdempotencySnapshot(
            response_status=202,
            response={
                "recompute_request_id": str(RECOMPUTE_ID),
                "state": "PENDING",
                "status_url": f"/api/v1/recomputations/{RECOMPUTE_ID}",
            },
        )


def test_analysis_request_returns_an_actual_status_url_for_the_authenticated_principal() -> None:
    # Given
    service = StaticAnalysisService()
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            analysis_service=service,
        ),
    )

    # When
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analyses",
            json={
                "goal_id": str(GOAL_ID),
                "expected_profile_version": 3,
                "basis_type": "EDITORIAL",
            },
        )

    # Then
    assert response.status_code == 202
    assert response.json() == {
        "recompute_request_id": str(RECOMPUTE_ID),
        "state": "PENDING",
        "status_url": f"/api/v1/recomputations/{RECOMPUTE_ID}",
    }
    assert service.command == AnalysisRequestCommand(
        goal_id=GOAL_ID,
        expected_profile_version=3,
        basis_type="EDITORIAL",
    )


def test_native_source_rejects_analysis_before_an_injected_service_can_enqueue() -> None:
    # Given
    service = StaticAnalysisService()
    app = create_app(
        Settings(
            enable_fixtures=False,
            corpus_source="neo4j_query_api",
            db_link=SecretStr("neo4j+s://reader@graph.example.test"),
            db_password=SecretStr("synthetic-query-api-password"),
        ),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            analysis_service=service,
        ),
    )

    # When
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analyses",
            json={
                "goal_id": str(GOAL_ID),
                "expected_profile_version": 3,
                "basis_type": "EDITORIAL",
            },
        )

    # Then
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"
    assert service.command is None


def test_analysis_request_replays_an_idempotent_response_without_running_the_service() -> None:
    # Given
    service = StaticAnalysisService()
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            analysis_service=service,
            idempotency_store=ReplayIdempotencyStore(),
        ),
    )

    # When
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analyses",
            headers={"Idempotency-Key": "analysis-request-1"},
            json={
                "goal_id": str(GOAL_ID),
                "expected_profile_version": 3,
                "basis_type": "EDITORIAL",
            },
        )

    # Then
    assert response.status_code == 202
    assert response.json() == {
        "recompute_request_id": str(RECOMPUTE_ID),
        "state": "PENDING",
        "status_url": f"/api/v1/recomputations/{RECOMPUTE_ID}",
    }
    assert service.command is None


def test_analysis_request_builds_and_submits_an_immutable_context() -> None:
    # Given
    context = _context_document()
    submitter = RecordingAnalysisSubmitter()
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(
            identity_provider=StaticIdentityProvider(),
            analysis_context_factory=StaticAnalysisContextFactory(context),
            analysis_recompute_submitter=submitter,
        ),
    )

    # When
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analyses",
            json={
                "goal_id": str(GOAL_ID),
                "expected_profile_version": 3,
                "basis_type": "EDITORIAL",
            },
        )

    # Then
    assert response.status_code == 202
    assert response.json()["recompute_request_id"] == str(RECOMPUTE_ID)
    assert submitter.submission is not None
    assert submitter.submission.user_id == USER_ID
    assert submitter.submission.goal_id == GOAL_ID
    assert submitter.submission.expected_profile_version == 3
    assert submitter.submission.context == context.model_dump(mode="json")
    assert submitter.submission.payload == {"basis_type": "EDITORIAL", "source": "analysis-api"}

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import anyio

from jobtology_be.application.services.analyses import (
    AnalysisRequestCommand,
    PersistentAnalysisService,
)
from jobtology_be.infrastructure.persistence.contracts import (
    AnalysisRecomputeSubmission,
    RecomputeRequestSnapshot,
)
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.workers.context import RecomputeContextDocument

USER_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
GOAL_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b5")
RECOMPUTE_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b6")
REFERENCE_AT = datetime(2026, 9, 22, 12, tzinfo=UTC)


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
            "calendar": (
                {
                    "starts_at": REFERENCE_AT,
                    "ends_at": datetime(2026, 9, 22, 13, tzinfo=UTC),
                    "capacity_week_key": "2026-W39",
                },
            ),
        }
    )


@dataclass(frozen=True, slots=True)
class StaticContextFactory:
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


def test_persistent_analysis_service_creates_a_context_bound_recompute_request() -> None:
    # Given
    context = _context_document()
    submitter = RecordingAnalysisSubmitter()
    service = PersistentAnalysisService(StaticContextFactory(context), submitter)
    command = AnalysisRequestCommand(
        goal_id=GOAL_ID,
        expected_profile_version=3,
        basis_type="EDITORIAL",
    )

    # When
    result = anyio.run(service.request, USER_ID, command)

    # Then
    assert result.recompute_request_id == RECOMPUTE_ID
    assert result.state == "PENDING"
    assert submitter.submission is not None
    assert submitter.submission.user_id == USER_ID
    assert submitter.submission.goal_id == GOAL_ID
    assert submitter.submission.expected_profile_version == 3
    assert submitter.submission.payload == {"basis_type": "EDITORIAL", "source": "analysis-api"}
    assert submitter.submission.context == context.model_dump(mode="json")
    assert submitter.submission.dedupe_key.startswith("analysis:")

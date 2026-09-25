from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID, uuid4

from jobtology_be.infrastructure.persistence.contracts import (
    AnalysisRecomputeSubmission,
    RecomputeRequestSnapshot,
)
from jobtology_be.workers.context import RecomputeContextDocument


@dataclass(frozen=True, slots=True)
class AnalysisRequestCommand:
    goal_id: UUID
    expected_profile_version: int
    basis_type: Literal["EDITORIAL"]
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class AnalysisRequestResult:
    recompute_request_id: UUID
    state: str


class AnalysisContextFactory(Protocol):
    async def create_context(
        self, user_id: UUID, command: AnalysisRequestCommand
    ) -> RecomputeContextDocument: ...


class AnalysisRecomputeSubmitter(Protocol):
    async def submit_analysis_recompute(
        self, submission: AnalysisRecomputeSubmission
    ) -> RecomputeRequestSnapshot: ...


class AnalysisService(Protocol):
    async def request(self, user_id: UUID, command: AnalysisRequestCommand) -> AnalysisRequestResult: ...


class PersistentAnalysisService:
    _context_factory: AnalysisContextFactory
    _submitter: AnalysisRecomputeSubmitter

    def __init__(
        self,
        context_factory: AnalysisContextFactory,
        submitter: AnalysisRecomputeSubmitter,
    ) -> None:
        self._context_factory = context_factory
        self._submitter = submitter

    async def request(self, user_id: UUID, command: AnalysisRequestCommand) -> AnalysisRequestResult:
        context = await self._context_factory.create_context(user_id, command)
        dedupe_key = (
            f"analysis:{uuid4()}"
            if command.idempotency_key is None
            else f"analysis:{sha256(f'{user_id}:{command.idempotency_key}'.encode()).hexdigest()}"
        )
        request = await self._submitter.submit_analysis_recompute(
            AnalysisRecomputeSubmission(
                user_id=user_id,
                goal_id=command.goal_id,
                expected_profile_version=command.expected_profile_version,
                dedupe_key=dedupe_key,
                payload={"basis_type": command.basis_type, "source": "analysis-api"},
                context=context.model_dump(mode="json"),
            )
        )
        return AnalysisRequestResult(
            recompute_request_id=request.request_id,
            state=request.state,
        )

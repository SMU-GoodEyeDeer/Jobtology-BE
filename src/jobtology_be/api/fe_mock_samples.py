from typing import ClassVar, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from jobtology_be.api.analyses import AnalysisRequest, AnalysisRequestResponse
from jobtology_be.api.errors import ErrorBody, ErrorResponse

router = APIRouter(tags=["FE mock samples"])


class FrontendMockSamplesResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    fixture_kind: Literal["FE_MOCK_SAMPLES"]
    read_only: Literal[True]
    contract_scope: Literal["MOCK_ONLY_NOT_PRODUCT_DATA"]
    legacy_preview_paths: tuple[str, ...]
    analysis_request: AnalysisRequest
    analysis_accepted: AnalysisRequestResponse
    idempotency_replay: AnalysisRequestResponse
    unauthenticated: ErrorResponse
    version_conflict: ErrorResponse


@router.get(
    "/samples",
    response_model=FrontendMockSamplesResponse,
    summary="Get read-only FE mock samples",
    description=(
        "Returns static mock-only DTO payloads for frontend testing. These are distinct from legacy "
        "preview fixtures and never represent persisted product data."
    ),
)
def get_frontend_mock_samples() -> FrontendMockSamplesResponse:
    recompute_request_id = UUID("00000000-0000-0000-0000-000000000101")
    accepted = AnalysisRequestResponse(
        recompute_request_id=recompute_request_id,
        state="PENDING",
        status_url=f"/api/v1/recomputations/{recompute_request_id}",
    )
    return FrontendMockSamplesResponse(
        fixture_kind="FE_MOCK_SAMPLES",
        read_only=True,
        contract_scope="MOCK_ONLY_NOT_PRODUCT_DATA",
        legacy_preview_paths=("/api/v1/dev/analysis", "/api/v1/dev/route-proposal"),
        analysis_request=AnalysisRequest(
            goal_id=UUID("00000000-0000-0000-0000-000000000001"),
            expected_profile_version=3,
            basis_type="EDITORIAL",
        ),
        analysis_accepted=accepted,
        idempotency_replay=accepted,
        unauthenticated=ErrorResponse(
            error=ErrorBody(
                code="UNAUTHENTICATED",
                message="Unauthorized",
                request_id=UUID("00000000-0000-0000-0000-000000000401"),
            )
        ),
        version_conflict=ErrorResponse(
            error=ErrorBody(
                code="VERSION_CONFLICT",
                message="Request conflicts with current state",
                request_id=UUID("00000000-0000-0000-0000-000000000409"),
            )
        ),
    )

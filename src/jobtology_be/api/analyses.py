from datetime import datetime
from typing import Annotated, ClassVar, Literal, assert_never
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt
from starlette.responses import JSONResponse

from jobtology_be.api.errors import ErrorResponse
from jobtology_be.api.idempotency import (
    IdempotencyReplay,
    IdempotencyRequest,
    IdempotencyStore,
    execute_if_requested,
    require_idempotency_store,
)
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.product_queries import require_product_queries
from jobtology_be.application.queries import AnalysisView, ProductQueries, RecomputeView
from jobtology_be.application.services.analyses import (
    AnalysisRequestCommand,
    AnalysisService,
)

router = APIRouter(tags=["analyses"])


class AnalysisRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "goal_id": "00000000-0000-0000-0000-000000000001",
                    "expected_profile_version": 3,
                    "basis_type": "EDITORIAL",
                }
            ]
        },
    )

    goal_id: UUID
    expected_profile_version: StrictInt = Field(ge=1)
    basis_type: Literal["EDITORIAL"]


class AnalysisRequestResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    recompute_request_id: UUID
    state: str
    status_url: str


class RecomputeResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    recompute_request_id: UUID
    profile_version: int
    state: str
    resulting_analysis_id: UUID | None
    proposal_id: UUID | None
    error_code: str | None


class AnalysisResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    analysis_id: UUID
    goal_id: UUID
    profile_version: int
    basis_type: str
    basis_version: str
    release_id: str | None
    methodology_version: str
    status: str
    reference_at: datetime
    results: dict[str, JsonValue] | None


async def require_analysis_service() -> AnalysisService:
    raise HTTPException(status_code=503)


def _recompute_response(result: RecomputeView) -> RecomputeResponse:
    return RecomputeResponse(
        recompute_request_id=result.recompute_request_id,
        profile_version=result.profile_version,
        state=result.state,
        resulting_analysis_id=result.resulting_analysis_id,
        proposal_id=result.proposal_id,
        error_code=result.error_code,
    )


def _analysis_response(result: AnalysisView) -> AnalysisResponse:
    return AnalysisResponse(
        analysis_id=result.analysis_id,
        goal_id=result.goal_id,
        profile_version=result.profile_version,
        basis_type=result.basis_type,
        basis_version=result.basis_version,
        release_id=result.release_id,
        methodology_version=result.methodology_version,
        status=result.status,
        reference_at=result.reference_at,
        results=result.results,
    )


def _analysis_command(request: AnalysisRequest, idempotency_key: str | None) -> AnalysisRequestCommand:
    return AnalysisRequestCommand(
        goal_id=request.goal_id,
        expected_profile_version=request.expected_profile_version,
        basis_type=request.basis_type,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/analyses",
    response_model=AnalysisRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue analysis recomputation",
    description=(
        "Creates an asynchronous recomputation request. Poll the returned `status_url` until the "
        "recompute reaches a terminal state, then use the resulting analysis or proposal identifier. "
        "An `Idempotency-Key` replays the first accepted response for the same payload for 24 hours."
    ),
    responses={
        202: {
            "description": "Recomputation was accepted for asynchronous processing.",
            "content": {
                "application/json": {
                    "example": {
                        "recompute_request_id": "00000000-0000-0000-0000-000000000101",
                        "state": "PENDING",
                        "status_url": "/api/v1/recomputations/00000000-0000-0000-0000-000000000101",
                    }
                }
            },
        },
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {"model": ErrorResponse, "description": "The request is not permitted."},
        409: {"model": ErrorResponse, "description": "Version or idempotency conflict."},
        503: {"model": ErrorResponse, "description": "Analysis inputs are unavailable."},
    },
)
async def request_analysis(
    request: AnalysisRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    analysis_service: Annotated[AnalysisService, Depends(require_analysis_service)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            description="Optional 1-255 character key for 24-hour same-payload response replay.",
        )
    ] = None,
    idempotency_store: Annotated[IdempotencyStore | None, Depends(require_idempotency_store)] = None,
) -> AnalysisRequestResponse | JSONResponse:
    async def operation() -> AnalysisRequestResponse:
        result = await analysis_service.request(
            principal.user_id, _analysis_command(request, idempotency_key)
        )
        return AnalysisRequestResponse(
            recompute_request_id=result.recompute_request_id,
            state=result.state,
            status_url=f"/api/v1/recomputations/{result.recompute_request_id}",
        )

    idempotency_result: AnalysisRequestResponse | IdempotencyReplay = await execute_if_requested(
        idempotency_store,
        None
        if idempotency_key is None
        else IdempotencyRequest(
            user_id=principal.user_id,
            method="POST",
            path="/api/v1/analyses",
            key=idempotency_key,
            payload=request,
        ),
        status.HTTP_202_ACCEPTED,
        operation,
    )
    match idempotency_result:
        case IdempotencyReplay(response_status=response_status, response=response):
            return JSONResponse(status_code=response_status, content=dict(response))
        case AnalysisRequestResponse() as response:
            return response
        case unreachable:
            assert_never(unreachable)


@router.get(
    "/recomputations/{recompute_request_id}",
    response_model=RecomputeResponse,
    summary="Poll analysis recomputation status",
    description="Poll the accepted recomputation request until it reaches a terminal state.",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {"model": ErrorResponse, "description": "The request is not permitted."},
        404: {"model": ErrorResponse, "description": "The recomputation request was not found."},
        503: {"model": ErrorResponse, "description": "Product data is unavailable."},
    },
)
async def get_recompute(
    recompute_request_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    product_queries: Annotated[ProductQueries, Depends(require_product_queries)],
) -> RecomputeResponse:
    return _recompute_response(
        await product_queries.get_recompute(principal.user_id, recompute_request_id)
    )


@router.get(
    "/analyses/{analysis_id}",
    response_model=AnalysisResponse,
    summary="Get analysis result",
    description="Returns the persisted analysis result. `results` can be null until analysis output is ready.",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {"model": ErrorResponse, "description": "The request is not permitted."},
        404: {"model": ErrorResponse, "description": "The analysis was not found."},
        503: {"model": ErrorResponse, "description": "Product data is unavailable."},
    },
)
async def get_analysis(
    analysis_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    product_queries: Annotated[ProductQueries, Depends(require_product_queries)],
) -> AnalysisResponse:
    return _analysis_response(await product_queries.get_analysis(principal.user_id, analysis_id))

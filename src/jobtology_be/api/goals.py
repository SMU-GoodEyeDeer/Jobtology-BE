from typing import Annotated, ClassVar, Literal, assert_never
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    model_validator,
)
from starlette.responses import JSONResponse

from jobtology_be.api.idempotency import (
    IdempotencyReplay,
    IdempotencyRequest,
    IdempotencyStore,
    execute_if_requested,
    require_idempotency_store,
)
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.product_queries import require_product_queries
from jobtology_be.application.queries import GoalView, ProductQueries
from jobtology_be.application.services.goals import GoalService, GoalUpdateCommand, GoalUpdateResult

router = APIRouter(tags=["goals"])


class GoalUpdateRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    expected_profile_version: StrictInt = Field(ge=1)
    goal_mode: Literal["TARGETED", "DISCOVERY"]
    occupation_id: StrictStr | None = Field(default=None, max_length=128)
    target_by: AwareDatetime
    timezone: StrictStr = Field(min_length=1, max_length=100)
    original_time_phrase: StrictStr = Field(min_length=1, max_length=500)
    status: Literal["DRAFT", "ACTIVE", "ARCHIVED"] = "ACTIVE"

    @model_validator(mode="after")
    def validate_targeted_occupation(self) -> "GoalUpdateRequest":
        match self.goal_mode:
            case "TARGETED":
                if self.occupation_id is None:
                    raise ValueError("targeted goals require an occupation ID")
            case "DISCOVERY":
                pass
            case unreachable:
                assert_never(unreachable)
        return self


class GoalResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    goal_id: UUID
    status: Literal["DRAFT", "ACTIVE", "ARCHIVED"]


class GoalDetailResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    goal_id: UUID
    goal_mode: Literal["TARGETED", "DISCOVERY"]
    occupation_id: str | None
    target_by: AwareDatetime
    timezone: str
    original_time_phrase: str
    status: Literal["DRAFT", "ACTIVE", "ARCHIVED"]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class GoalListResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    items: tuple[GoalDetailResponse, ...]


async def require_goal_service() -> GoalService:
    raise HTTPException(status_code=503)


def _goal_response(result: GoalUpdateResult) -> GoalResponse:
    return GoalResponse(goal_id=result.goal_id, status=result.status)


def _goal_detail_response(result: GoalView) -> GoalDetailResponse:
    return GoalDetailResponse(
        goal_id=result.goal_id,
        goal_mode=result.goal_mode,
        occupation_id=result.occupation_id,
        target_by=result.target_by,
        timezone=result.timezone,
        original_time_phrase=result.original_time_phrase,
        status=result.status,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


def _goal_command(request: GoalUpdateRequest, goal_id: UUID | None) -> GoalUpdateCommand:
    return GoalUpdateCommand(
        goal_id=goal_id,
        expected_profile_version=request.expected_profile_version,
        goal_mode=request.goal_mode,
        occupation_id=request.occupation_id,
        target_by=request.target_by,
        timezone=request.timezone,
        original_time_phrase=request.original_time_phrase,
        status=request.status,
    )


@router.get(
    "/me/goals",
    response_model=GoalListResponse,
    summary="List current goals",
    description="Returns goals visible to the authenticated user.",
)
async def list_goals(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    product_queries: Annotated[ProductQueries, Depends(require_product_queries)],
) -> GoalListResponse:
    return GoalListResponse(
        items=tuple(_goal_detail_response(goal) for goal in await product_queries.list_goals(principal.user_id))
    )


@router.get(
    "/me/goals/{goal_id}",
    response_model=GoalDetailResponse,
    summary="Get current goal",
    description="Returns one goal visible to the authenticated user.",
)
async def get_goal(
    goal_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    product_queries: Annotated[ProductQueries, Depends(require_product_queries)],
) -> GoalDetailResponse:
    return _goal_detail_response(await product_queries.get_goal(principal.user_id, goal_id))


@router.post(
    "/me/goals",
    response_model=GoalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create goal",
    description="Creates a goal. An optional `Idempotency-Key` replays the first same-payload response for 24 hours.",
)
async def create_goal(
    request: GoalUpdateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    goal_service: Annotated[GoalService, Depends(require_goal_service)],
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
) -> GoalResponse | JSONResponse:
    async def operation() -> GoalResponse:
        return _goal_response(
            await goal_service.update_goal(principal.user_id, _goal_command(request, None))
        )

    idempotency_result: GoalResponse | IdempotencyReplay = await execute_if_requested(
        idempotency_store,
        None
        if idempotency_key is None
        else IdempotencyRequest(
            user_id=principal.user_id,
            method="POST",
            path="/api/v1/me/goals",
            key=idempotency_key,
            payload=request,
        ),
        status.HTTP_201_CREATED,
        operation,
    )
    match idempotency_result:
        case IdempotencyReplay(response_status=response_status, response=response):
            return JSONResponse(status_code=response_status, content=dict(response))
        case GoalResponse() as response:
            return response
        case unreachable:
            assert_never(unreachable)


@router.patch(
    "/me/goals/{goal_id}",
    response_model=GoalResponse,
    summary="Update goal",
    description="Updates a goal using `expected_profile_version` for optimistic concurrency.",
)
async def update_goal(
    goal_id: UUID,
    request: GoalUpdateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    goal_service: Annotated[GoalService, Depends(require_goal_service)],
) -> GoalResponse:
    return _goal_response(await goal_service.update_goal(principal.user_id, _goal_command(request, goal_id)))

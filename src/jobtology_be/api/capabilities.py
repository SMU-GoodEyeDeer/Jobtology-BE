from typing import Annotated, ClassVar, assert_never
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, StrictInt, StrictStr
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
from jobtology_be.application.queries import CapabilityView, ProductQueries
from jobtology_be.application.services.capabilities import (
    CapabilityMutationCommand,
    CapabilityResult,
    CapabilityService,
)

router = APIRouter(tags=["capabilities"])


class CapabilityMutationRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    expected_profile_version: StrictInt = Field(ge=1)
    category: StrictStr = Field(min_length=1, max_length=64)
    raw_text: StrictStr = Field(min_length=1, max_length=500)
    entity_id: StrictStr | None = Field(default=None, max_length=128)
    proficiency: StrictStr | None = Field(default=None, max_length=32)
    details: dict[str, JsonValue] = Field(default_factory=dict)


class CapabilityDeleteRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    expected_profile_version: StrictInt = Field(ge=1)


class CapabilityResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    capability_id: UUID
    profile_version: int = Field(ge=1)


class CapabilityDetailResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    capability_id: UUID
    category: str
    raw_text: str
    entity_id: str | None
    proficiency: str | None
    verification: str
    lifecycle: str
    details: dict[str, JsonValue]
    source_completion_event_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class CapabilityListResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    items: tuple[CapabilityDetailResponse, ...]


class CapabilityDeleteResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    capability_id: UUID
    profile_version: int = Field(ge=1)


async def require_capability_service() -> CapabilityService:
    raise HTTPException(status_code=503)


def _command(request: CapabilityMutationRequest, capability_id: UUID | None) -> CapabilityMutationCommand:
    return CapabilityMutationCommand(
        capability_id=capability_id,
        expected_profile_version=request.expected_profile_version,
        category=request.category,
        raw_text=request.raw_text,
        entity_id=request.entity_id,
        proficiency=request.proficiency,
        details=request.details,
    )


def _response(result: CapabilityResult) -> CapabilityResponse:
    return CapabilityResponse(
        capability_id=result.capability_id,
        profile_version=result.profile_version,
    )


def _detail_response(result: CapabilityView) -> CapabilityDetailResponse:
    return CapabilityDetailResponse(
        capability_id=result.capability_id,
        category=result.category,
        raw_text=result.raw_text,
        entity_id=result.entity_id,
        proficiency=result.proficiency,
        verification=result.verification,
        lifecycle=result.lifecycle,
        details=dict(result.details),
        source_completion_event_id=result.source_completion_event_id,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


@router.get(
    "/me/capabilities",
    response_model=CapabilityListResponse,
    summary="List current capabilities",
    description="Returns active capabilities visible to the authenticated user.",
)
async def list_capabilities(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    product_queries: Annotated[ProductQueries, Depends(require_product_queries)],
) -> CapabilityListResponse:
    return CapabilityListResponse(
        items=tuple(
            _detail_response(capability)
            for capability in await product_queries.list_capabilities(principal.user_id)
        )
    )


@router.get(
    "/me/capabilities/{capability_id}",
    response_model=CapabilityDetailResponse,
    summary="Get current capability",
    description="Returns one capability visible to the authenticated user.",
)
async def get_capability(
    capability_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    product_queries: Annotated[ProductQueries, Depends(require_product_queries)],
) -> CapabilityDetailResponse:
    return _detail_response(
        await product_queries.get_capability(principal.user_id, capability_id)
    )


@router.post(
    "/me/capabilities",
    response_model=CapabilityResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create capability",
    description="Creates a capability. An optional `Idempotency-Key` replays the first same-payload response for 24 hours.",
)
async def create_capability(
    request: CapabilityMutationRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    capability_service: Annotated[CapabilityService, Depends(require_capability_service)],
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
) -> CapabilityResponse | JSONResponse:
    async def operation() -> CapabilityResponse:
        return _response(await capability_service.upsert(principal.user_id, _command(request, None)))

    idempotency_result: CapabilityResponse | IdempotencyReplay = await execute_if_requested(
        idempotency_store,
        None
        if idempotency_key is None
        else IdempotencyRequest(
            user_id=principal.user_id,
            method="POST",
            path="/api/v1/me/capabilities",
            key=idempotency_key,
            payload=request,
        ),
        status.HTTP_201_CREATED,
        operation,
    )
    match idempotency_result:
        case IdempotencyReplay(response_status=response_status, response=response):
            return JSONResponse(status_code=response_status, content=dict(response))
        case CapabilityResponse() as response:
            return response
        case unreachable:
            assert_never(unreachable)


@router.patch(
    "/me/capabilities/{capability_id}",
    response_model=CapabilityResponse,
    summary="Update capability",
    description="Updates one capability using `expected_profile_version` for optimistic concurrency.",
)
async def update_capability(
    capability_id: UUID,
    request: CapabilityMutationRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    capability_service: Annotated[CapabilityService, Depends(require_capability_service)],
) -> CapabilityResponse:
    return _response(
        await capability_service.upsert(principal.user_id, _command(request, capability_id))
    )


@router.delete(
    "/me/capabilities/{capability_id}",
    response_model=CapabilityDeleteResponse,
    summary="Delete capability",
    description="Deletes one capability using `expected_profile_version` for optimistic concurrency.",
)
async def delete_capability(
    capability_id: UUID,
    request: CapabilityDeleteRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    capability_service: Annotated[CapabilityService, Depends(require_capability_service)],
) -> CapabilityDeleteResponse:
    profile_version = await capability_service.delete(
        principal.user_id, capability_id, request.expected_profile_version
    )
    return CapabilityDeleteResponse(capability_id=capability_id, profile_version=profile_version)

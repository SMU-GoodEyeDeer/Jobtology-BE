from typing import Annotated, ClassVar, assert_never

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from starlette.responses import JSONResponse

from jobtology_be.api.capabilities import require_capability_service
from jobtology_be.api.idempotency import (
    IdempotencyReplay,
    IdempotencyRequest,
    IdempotencyStore,
    execute_if_requested,
    require_idempotency_store,
)
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.application.m5_queries import M5DataUnavailableError
from jobtology_be.application.services.capabilities import (
    CapabilityService,
    OnboardingCapabilitiesCommand,
)
from jobtology_be.infrastructure.persistence.contracts import OnboardingCapabilityUnit
from jobtology_be.product_roles.checklist import (
    ChecklistOccupationUnavailableError,
    OccupationChecklist,
    OnboardingChecklistCatalog,
    UnknownChecklistItemError,
)

router = APIRouter(tags=["capabilities"])

_MAX_ITEMS = 64


class ChecklistItemResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    label: str


class ChecklistGroupResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    label: str
    items: tuple[ChecklistItemResponse, ...]


class CapabilityChecklistResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    occupation_id: str
    checklist_version: int
    groups: tuple[ChecklistGroupResponse, ...]


class OnboardingCapabilitiesRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    expected_profile_version: StrictInt = Field(ge=1)
    occupation_id: StrictStr = Field(min_length=1, max_length=128)
    item_ids: tuple[StrictStr, ...] = Field(max_length=_MAX_ITEMS)


class OnboardingCapabilitiesResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    profile_version: int = Field(ge=1)
    saved_count: int = Field(ge=0)


async def require_onboarding_checklist() -> OnboardingChecklistCatalog:
    raise HTTPException(status_code=503)


def _checklist(catalog: OnboardingChecklistCatalog, occupation_id: str) -> OccupationChecklist:
    try:
        return catalog.checklist(occupation_id)
    except ChecklistOccupationUnavailableError as error:
        raise HTTPException(status_code=404) from error
    except M5DataUnavailableError as error:
        raise HTTPException(status_code=503) from error


@router.get(
    "/occupations/{occupation_id}/capability-checklist",
    response_model=CapabilityChecklistResponse,
    summary="Get onboarding capability checklist",
    description=(
        "Lists the approved onboarding checklist items for one occupation. Items whose linked "
        "requirements are absent from the current role release are hidden."
    ),
)
async def get_capability_checklist(
    occupation_id: str,
    catalog: Annotated[OnboardingChecklistCatalog, Depends(require_onboarding_checklist)],
) -> CapabilityChecklistResponse:
    checklist = _checklist(catalog, occupation_id)
    return CapabilityChecklistResponse(
        occupation_id=checklist.occupation_id,
        checklist_version=checklist.version,
        groups=tuple(
            ChecklistGroupResponse(
                label=group.label,
                items=tuple(
                    ChecklistItemResponse(item_id=item.item_id, label=item.label)
                    for item in group.items
                ),
            )
            for group in checklist.groups
        ),
    )


@router.put(
    "/me/capabilities/onboarding",
    response_model=OnboardingCapabilitiesResponse,
    summary="Replace onboarding capabilities",
    description=(
        "Replaces the user's onboarding checklist answers in one transaction and bumps the "
        "profile version once. Answers are stored as self-reported capabilities; capabilities "
        "entered elsewhere are not changed. An empty `item_ids` clears previous answers. "
        "An optional `Idempotency-Key` replays the first same-payload response for 24 hours."
    ),
)
async def replace_onboarding_capabilities(
    request: OnboardingCapabilitiesRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    capability_service: Annotated[CapabilityService, Depends(require_capability_service)],
    catalog: Annotated[OnboardingChecklistCatalog, Depends(require_onboarding_checklist)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            description="Optional 1-255 character key for 24-hour same-payload response replay.",
        ),
    ] = None,
    idempotency_store: Annotated[IdempotencyStore | None, Depends(require_idempotency_store)] = None,
) -> OnboardingCapabilitiesResponse | JSONResponse:
    checklist = _checklist(catalog, request.occupation_id)
    try:
        units = catalog.resolve(request.occupation_id, request.item_ids)
    except UnknownChecklistItemError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    async def operation() -> OnboardingCapabilitiesResponse:
        profile_version = await capability_service.replace_onboarding(
            principal.user_id,
            OnboardingCapabilitiesCommand(
                expected_profile_version=request.expected_profile_version,
                occupation_id=request.occupation_id,
                checklist_version=checklist.version,
                units=tuple(
                    OnboardingCapabilityUnit(item_id=unit.item_id, raw_text=unit.raw_text)
                    for unit in units
                ),
            ),
        )
        return OnboardingCapabilitiesResponse(
            profile_version=profile_version, saved_count=len(units)
        )

    result: OnboardingCapabilitiesResponse | IdempotencyReplay = await execute_if_requested(
        idempotency_store,
        None
        if idempotency_key is None
        else IdempotencyRequest(
            user_id=principal.user_id,
            method="PUT",
            path="/api/v1/me/capabilities/onboarding",
            key=idempotency_key,
            payload=request,
        ),
        status.HTTP_200_OK,
        operation,
    )
    match result:
        case IdempotencyReplay(response_status=response_status, response=response):
            return JSONResponse(status_code=response_status, content=dict(response))
        case OnboardingCapabilitiesResponse() as response:
            return response
        case unreachable:
            assert_never(unreachable)

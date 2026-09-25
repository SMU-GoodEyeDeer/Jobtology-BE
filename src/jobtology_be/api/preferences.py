from typing import Annotated, ClassVar, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.application.services.preferences import (
    PreferencesService,
    PreferencesUpdateCommand,
    RoutePreferences,
)

router = APIRouter(tags=["preferences"])


class RoutePreferencesUpdateRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    expected_profile_version: StrictInt = Field(ge=1)
    available_hours_per_week: StrictInt = Field(ge=1, le=60)
    availability_source: StrictStr = Field(min_length=1, max_length=64)
    budget_mode: Literal["REGULAR", "LOW_COST"]
    max_out_of_pocket_krw: StrictInt | None = Field(default=None, ge=0)
    fastest_path: StrictBool
    needs_portfolio: StrictBool
    career_switch: StrictBool


class RoutePreferencesResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    profile_version: int = Field(ge=1)
    available_hours_per_week: int = Field(ge=1, le=60)
    availability_source: str
    budget_mode: Literal["REGULAR", "LOW_COST"]
    max_out_of_pocket_krw: int | None
    fastest_path: bool
    needs_portfolio: bool
    career_switch: bool


async def require_preferences_service() -> PreferencesService:
    raise HTTPException(status_code=503)


def _response(preferences: RoutePreferences) -> RoutePreferencesResponse:
    return RoutePreferencesResponse(
        profile_version=preferences.profile_version,
        available_hours_per_week=preferences.available_hours_per_week,
        availability_source=preferences.availability_source,
        budget_mode=preferences.budget_mode,
        max_out_of_pocket_krw=preferences.max_out_of_pocket_krw,
        fastest_path=preferences.fastest_path,
        needs_portfolio=preferences.needs_portfolio,
        career_switch=preferences.career_switch,
    )


@router.get(
    "/me/route-preferences",
    response_model=RoutePreferencesResponse,
    summary="Get route preferences",
    description="Returns the authenticated user's current route planning preferences.",
)
async def get_route_preferences(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    preferences_service: Annotated[PreferencesService, Depends(require_preferences_service)],
) -> RoutePreferencesResponse:
    return _response(await preferences_service.get_preferences(principal.user_id))


@router.put(
    "/me/route-preferences",
    response_model=RoutePreferencesResponse,
    summary="Replace route preferences",
    description="Replaces route planning preferences using `expected_profile_version` for optimistic concurrency.",
)
async def update_route_preferences(
    request: RoutePreferencesUpdateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    preferences_service: Annotated[PreferencesService, Depends(require_preferences_service)],
) -> RoutePreferencesResponse:
    return _response(
        await preferences_service.update_preferences(
            principal.user_id,
            PreferencesUpdateCommand(
                expected_profile_version=request.expected_profile_version,
                available_hours_per_week=request.available_hours_per_week,
                availability_source=request.availability_source,
                budget_mode=request.budget_mode,
                max_out_of_pocket_krw=request.max_out_of_pocket_krw,
                fastest_path=request.fastest_path,
                needs_portfolio=request.needs_portfolio,
                career_switch=request.career_switch,
            ),
        )
    )

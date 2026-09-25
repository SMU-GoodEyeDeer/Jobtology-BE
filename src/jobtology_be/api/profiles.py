from datetime import date
from typing import Annotated, ClassVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.product_queries import require_product_queries
from jobtology_be.application.queries import ProductQueries
from jobtology_be.application.services.profiles import (
    ProfileService,
    ProfileUpdateCommand,
)

router = APIRouter(tags=["profiles"])


class ProfileUpdateRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    expected_profile_version: StrictInt = Field(ge=1)
    major_raw: StrictStr = Field(min_length=1, max_length=500)
    major_concept_id: StrictStr | None = Field(default=None, max_length=128)
    year: StrictInt | None = Field(default=None, ge=1)
    enrollment_status: StrictStr | None = Field(default=None, max_length=32)
    expected_graduation_on: date | None = None


class ProfileResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    user_id: UUID
    profile_version: int = Field(ge=1)


class ProfileDetailResponse(ProfileResponse):
    major_raw: str
    major_concept_id: str | None
    year: int | None
    enrollment_status: str | None
    expected_graduation_on: date | None


async def require_profile_service() -> ProfileService:
    raise HTTPException(status_code=503)


@router.get(
    "/me/profile",
    response_model=ProfileDetailResponse,
    summary="Get current profile",
    description="Returns the authenticated user's profile and its current version.",
)
async def get_profile(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    product_queries: Annotated[ProductQueries, Depends(require_product_queries)],
) -> ProfileDetailResponse:
    profile = await product_queries.get_profile(principal.user_id)
    return ProfileDetailResponse(
        user_id=profile.user_id,
        profile_version=profile.profile_version,
        major_raw=profile.major_raw,
        major_concept_id=profile.major_concept_id,
        year=profile.year,
        enrollment_status=profile.enrollment_status,
        expected_graduation_on=profile.expected_graduation_on,
    )


@router.put(
    "/me/profile",
    response_model=ProfileResponse,
    summary="Replace current profile",
    description="Replaces profile fields using `expected_profile_version`; a stale version returns a conflict.",
)
async def update_profile(
    request: ProfileUpdateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    profile_service: Annotated[ProfileService, Depends(require_profile_service)],
) -> ProfileResponse:
    result = await profile_service.update_profile(
        principal.user_id,
        ProfileUpdateCommand(
            expected_profile_version=request.expected_profile_version,
            major_raw=request.major_raw,
            major_concept_id=request.major_concept_id,
            year=request.year,
            enrollment_status=request.enrollment_status,
            expected_graduation_on=request.expected_graduation_on,
        ),
    )
    return ProfileResponse(user_id=result.user_id, profile_version=result.profile_version)

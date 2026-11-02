"""Private server-to-server administration, never proxied through the public FE."""

from hmac import compare_digest
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from jobtology_be.api.google_auth import require_google_settings
from jobtology_be.infrastructure.persistence.management import (
    ManagementConflict,
    ManagementNotFound,
    PostgresManagementStore,
)
from jobtology_be.settings import Settings


async def require_management_store() -> PostgresManagementStore:
    raise HTTPException(503, "Application management unavailable")


async def authorize_management(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(require_google_settings)],
) -> None:
    response.headers["Cache-Control"] = "no-store"
    secret = settings.management_token
    if secret is None:
        raise HTTPException(503, "Application management disabled")
    value = request.headers.get("Authorization", "")
    if len(value) > 1024 or not compare_digest(
        value.encode("utf-8"), ("Bearer " + secret.get_secret_value()).encode("utf-8")
    ):
        raise HTTPException(401, "Management authentication required")


router = APIRouter(
    prefix="/internal/admin", include_in_schema=False, dependencies=[Depends(authorize_management)]
)
Store = Annotated[PostgresManagementStore, Depends(require_management_store)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1000000)]


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    label: str = Field(min_length=1, max_length=100)
    expires_at: AwareDatetime | None = None
    access_hours: float | None = Field(default=None, gt=0, le=876000)
    max_uses: int | None = Field(default=None, gt=0, le=1000000, strict=True)


class InvitationUpdate(InvitationCreate):
    label: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def label_not_null(self):
        if "label" in self.model_fields_set and self.label is None:
            raise ValueError("label cannot be null")
        return self


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ACTIVE", "DEACTIVATED"] | None = None
    access_expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def status_not_null(self):
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        return self


async def _write(call):
    try:
        return await call
    except ManagementNotFound:
        raise HTTPException(404, "Not found") from None
    except ManagementConflict as error:
        raise HTTPException(409, str(error)) from None


@router.get("/invitations")
async def invitations(store: Store, limit: Limit = 50, offset: Offset = 0):
    return await store.invitations(limit, offset)


@router.post("/invitations", status_code=201)
async def create_invitation(
    body: InvitationCreate,
    store: Store,
    settings: Annotated[Settings, Depends(require_google_settings)],
):
    if settings.frontend_url is None:
        raise HTTPException(503, "Frontend URL is not configured")
    result = await store.create_invitation(body.model_dump())
    result["invite_url"] = settings.frontend_url.rstrip("/") + "/#invite=" + quote(result["code"])
    return result


@router.patch("/invitations/{identifier}")
async def update_invitation(identifier: UUID, body: InvitationUpdate, store: Store):
    return await _write(store.update_invitation(identifier, body.model_dump(exclude_unset=True)))


@router.post("/invitations/{identifier}/revoke")
async def revoke_invitation(identifier: UUID, store: Store):
    await _write(store.revoke_invitation(identifier))
    return {"ok": True}


@router.get("/users")
async def users(store: Store, limit: Limit = 50, offset: Offset = 0):
    return await store.users(limit, offset)


@router.patch("/users/{identifier}")
async def update_user(identifier: UUID, body: UserUpdate, store: Store):
    await _write(store.update_user(identifier, body.model_dump(exclude_unset=True)))
    return {"ok": True}


@router.get("/sessions")
async def sessions(
    store: Store, user_id: UUID | None = None, limit: Limit = 50, offset: Offset = 0
):
    return await store.sessions(user_id, limit, offset)


@router.post("/sessions/{identifier}/revoke")
async def revoke_session(identifier: UUID, store: Store):
    await _write(store.revoke_sessions(session_id=identifier))
    return {"ok": True}


@router.post("/users/{identifier}/revoke-sessions")
async def revoke_user_sessions(identifier: UUID, store: Store):
    await _write(store.revoke_sessions(user_id=identifier))
    return {"ok": True}

from typing import Annotated, ClassVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

from jobtology_be.api.identity import AuthenticatedSession
from jobtology_be.modules.auth.session import SessionStore
from jobtology_be.modules.auth.session_cookies import clear_session_cookie

router = APIRouter(tags=["authentication"])


class SessionResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    user_id: UUID
    csrf_token: str
    profile_version: int | None = None


async def require_authenticated_session() -> AuthenticatedSession:
    raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})


async def require_session_store() -> SessionStore:
    raise HTTPException(status_code=503)


async def require_session_response(
    session: Annotated[AuthenticatedSession, Depends(require_authenticated_session)],
) -> SessionResponse:
    return SessionResponse(user_id=session.principal.user_id, csrf_token=session.csrf_token)


@router.get(
    "/auth/session",
    response_model=SessionResponse,
    response_model_exclude_none=True,
    summary="Get authenticated session",
    description="Returns the current session principal and CSRF token for authenticated browser requests.",
)
async def get_session(
    response: Response,
    session: Annotated[SessionResponse, Depends(require_session_response)],
) -> SessionResponse:
    response.headers["Cache-Control"] = "no-store"
    return session


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Log out authenticated session",
    description="Revokes the current session and clears its cookie. Mutating browser requests require CSRF protection.",
)
async def logout(
    response: Response,
    session: Annotated[AuthenticatedSession, Depends(require_authenticated_session)],
    session_store: Annotated[SessionStore, Depends(require_session_store)],
) -> None:
    await session_store.revoke_session(session.session_token_hash)
    clear_session_cookie(response)

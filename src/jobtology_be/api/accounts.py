from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.application.services.accounts import AccountService
from jobtology_be.modules.auth.session_cookies import (
    clear_oauth_attempt_cookie,
    clear_session_cookie,
)

router = APIRouter(tags=["authentication"])


async def require_account_service() -> AccountService:
    raise HTTPException(status_code=503)


@router.delete(
    "/me/account",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete current Jobtology account",
    description=(
        "Permanently deletes the authenticated user's Jobtology data, Google identity link, "
        "and all sessions. Does not delete the Google account. Requires session CSRF protection."
    ),
)
async def delete_account(
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[AccountService, Depends(require_account_service)],
) -> None:
    await service.delete_account(principal.user_id)
    response.headers["Cache-Control"] = "no-store"
    clear_session_cookie(response)
    clear_oauth_attempt_cookie(response)

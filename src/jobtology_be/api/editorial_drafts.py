from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Request

from jobtology_be.api.errors import ErrorResponse
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.editorial.reader import DraftReadService, OccupationRead

router = APIRouter(prefix="/editorial/occupations", tags=["editorial drafts"])
_RESPONSES: Final[dict[int | str, dict[str, object]]] = {
    401: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
    422: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
}


async def require_editorial_drafts() -> DraftReadService:
    raise HTTPException(status_code=503)


@router.get("", response_model=tuple[OccupationRead, ...], responses=_RESPONSES)
async def list_editorial_occupations(
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    drafts: Annotated[DraftReadService, Depends(require_editorial_drafts)],
) -> tuple[OccupationRead, ...]:
    if request.query_params:
        raise HTTPException(status_code=422)
    return drafts.list_occupations()


@router.get("/{occupation_id}", response_model=OccupationRead, responses=_RESPONSES)
async def get_editorial_occupation(
    occupation_id: str,
    request: Request,
    _: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    drafts: Annotated[DraftReadService, Depends(require_editorial_drafts)],
) -> OccupationRead:
    if request.query_params:
        raise HTTPException(status_code=422)
    result = drafts.get_occupation(occupation_id)
    if result is None:
        raise HTTPException(status_code=404)
    return result

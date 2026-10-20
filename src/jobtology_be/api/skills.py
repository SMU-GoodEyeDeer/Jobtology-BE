from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.application.specs import SkillCatalog, SkillItem

router = APIRouter(tags=['capabilities'])


def require_skill_catalog() -> SkillCatalog:
    return SkillCatalog(lambda: ())


class SkillSearchResponse(BaseModel):
    items: tuple[SkillItem, ...]
    total: int
    available_sources: tuple[str, ...]


@router.get('/skills', response_model=SkillSearchResponse, summary='Search canonical skills')
def search_skills(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    catalog: Annotated[SkillCatalog, Depends(require_skill_catalog)],
    q: Annotated[str, Query(max_length=200)] = '',
    occupation_id: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SkillSearchResponse:
    matches = catalog.search(q, occupation_id)
    return SkillSearchResponse(items=matches[:limit], total=len(matches),
                              available_sources=tuple(sorted({item.source for item in catalog.items()})))

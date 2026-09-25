from fastapi import APIRouter
from pydantic import BaseModel

from jobtology_be.api.auth_session import router as auth_session_router
from jobtology_be.api.product import router as product_router

router = APIRouter()
router.include_router(auth_session_router)
router.include_router(product_router)


class HealthResponse(BaseModel):
    status: str


@router.get(
    "/health/live",
    response_model=HealthResponse,
    tags=["health"],
    summary="Check process liveness",
    description="Checks whether the API process is running; it does not check database readiness.",
)
def liveness() -> HealthResponse:
    """Process liveness only; this does not assert database readiness."""
    return HealthResponse(status="ok")

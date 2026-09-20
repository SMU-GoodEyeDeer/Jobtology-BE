from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str


@router.get("/health/live", response_model=HealthResponse, tags=["health"])
def liveness() -> HealthResponse:
    """Process liveness only; this does not assert database readiness."""
    return HealthResponse(status="ok")

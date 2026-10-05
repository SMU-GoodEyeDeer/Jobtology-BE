from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from jobtology_be.api.auth_session import router as auth_session_router
from jobtology_be.api.composition import OPENAPI_TAGS
from jobtology_be.api.errors import ErrorResponse
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


def create_api_shell(
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]],
) -> FastAPI:
    return FastAPI(
        title="Jobtology API",
        summary="개인화 역량 분석과 로드맵을 위한 Jobtology API",
        description=(
            "제품 API는 `/api/v1` 경로군에 적용됩니다. `/api/v2`는 명시적으로 구성된 "
            "Neo4j 원본 및 별도 승인된 PostgreSQL 소스 카탈로그의 읽기 전용 계약입니다. "
            "모든 제품 API는 인증된 세션을 요구하며, "
            "Google 로그인은 기본적으로 비활성화되어 있어 인증되지 않은 요청은 표준 `401` 오류 "
            "envelope을 반환합니다. 변경 요청에는 현재 버전을 제출하고, `Idempotency-Key`가 "
            "표시된 POST 요청은 같은 키와 payload에 대해 24시간 동안 최초 응답을 재생합니다. "
            "변경 요청은 세션 CSRF 보호를 위해 `X-CSRF-Token`도 필요합니다."
        ),
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        openapi_tags=OPENAPI_TAGS,
        responses={
            422: {
                "model": ErrorResponse,
                "description": "Request validation failed",
                "content": {
                    "application/json": {
                        "example": {
                            "error": {
                                "code": "VALIDATION_ERROR",
                                "message": "Request validation failed",
                                "details": [{"location": ["body", "field"], "code": "missing"}],
                                "request_id": "00000000-0000-0000-0000-000000000001",
                            }
                        }
                    }
                },
            }
        },
        lifespan=lifespan,
    )

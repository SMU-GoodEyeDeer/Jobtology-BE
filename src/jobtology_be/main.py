from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from jobtology_be.api.router import router
from jobtology_be.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Jobtology API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-CSRF-Token"],
    )
    app.include_router(router, prefix="/api/v1")
    if settings.enable_fixtures:
        from jobtology_be.api.fixtures import router as fixtures_router

        app.include_router(fixtures_router, prefix="/api/v1/dev")
    return app


app = create_app()

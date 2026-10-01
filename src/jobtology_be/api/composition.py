from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from jobtology_be.api.analyses import require_analysis_service
from jobtology_be.api.auth_session import require_authenticated_session, require_session_store
from jobtology_be.api.capabilities import require_capability_service
from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.editorial_drafts import require_editorial_drafts
from jobtology_be.api.goals import require_goal_service
from jobtology_be.api.idempotency import IdempotencyStore, require_idempotency_store
from jobtology_be.api.identity import SessionIdentityProvider, require_authenticated_principal
from jobtology_be.api.m5_queries import require_m5_queries
from jobtology_be.api.neo4j_catalog import Neo4jCatalogQueries, require_neo4j_catalog_queries
from jobtology_be.api.preferences import require_preferences_service
from jobtology_be.api.product_queries import require_product_queries
from jobtology_be.api.profiles import require_profile_service
from jobtology_be.api.roadmaps import require_roadmap_service
from jobtology_be.api.source_catalog import require_source_catalog
from jobtology_be.application.m5_queries import M5Queries
from jobtology_be.application.queries import ProductQueries
from jobtology_be.application.services.analyses import AnalysisService
from jobtology_be.application.services.capabilities import CapabilityService
from jobtology_be.application.services.goals import GoalService
from jobtology_be.application.services.preferences import PreferencesService
from jobtology_be.application.services.profiles import ProfileService
from jobtology_be.application.services.roadmaps import RoadmapService
from jobtology_be.corpus.source_factory import ConfiguredCorpusSource
from jobtology_be.editorial.reader import DraftReadService
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.source_catalog import (
    CatalogQueries,
    PostgresSourceCatalog,
)
from jobtology_be.modules.auth.session import SessionStore
from jobtology_be.settings import Settings

OPENAPI_TAGS = [
    {"name": "health", "description": "프로세스 liveness 상태를 확인합니다."},
    {"name": "authentication", "description": "세션 확인과 로그아웃을 제공합니다."},
    {"name": "profiles", "description": "사용자 프로필을 조회하거나 전체 갱신합니다."},
    {"name": "capabilities", "description": "사용자 역량을 관리합니다."},
    {"name": "preferences", "description": "경로 추천 선호도를 관리합니다."},
    {"name": "goals", "description": "진로 목표를 관리합니다."},
    {"name": "analyses", "description": "비동기 역량 분석 요청과 결과를 조회합니다."},
    {"name": "m5-queries", "description": "분석 결과, 제안, 추적 정보와 대시보드를 조회합니다."},
    {"name": "native catalog", "description": "Neo4j 원본 카탈로그의 안전한 읽기 전용 필드를 조회합니다."},
    {"name": "roadmaps", "description": "저장된 로드맵과 단계 상태를 관리합니다."},
    {"name": "development fixtures", "description": "명시적으로 활성화한 개발용 읽기 전용 예시입니다. 제품 데이터가 아닙니다."},
    {"name": "FE mock samples", "description": "명시적으로 활성화한 프론트엔드 mock 전용 읽기 전용 DTO 예시입니다."},
    {"name": "product", "description": "인증된 Jobtology 제품 API입니다."},
    {"name": "source catalog", "description": "승인된 PostgreSQL 소스 전용 읽기 계약입니다."},
    {"name": "editorial drafts", "description": "명시적으로 설정한 미검토 초안 읽기입니다."},
]


@asynccontextmanager
async def lifespan(
    _: FastAPI,
    database: Database | None,
    corpus_source: ConfiguredCorpusSource | None,
    source_catalog: PostgresSourceCatalog | None = None,
) -> AsyncIterator[None]:
    try:
        yield
    finally:
        try:
            if corpus_source is not None:
                await corpus_source.aclose()
        finally:
            try:
                if database is not None:
                    await database.dispose()
            finally:
                if source_catalog is not None:
                    await source_catalog.dispose()


def database_lifespan(
    database: Database | None,
    corpus_source: ConfiguredCorpusSource | None = None,
    source_catalog: PostgresSourceCatalog | None = None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    return lambda app: lifespan(app, database, corpus_source, source_catalog)


def register_api_dependencies(app: FastAPI, dependencies: ApiDependencies, settings: Settings) -> None:
    session_store = dependencies.session_store
    if session_store is not None:
        session_identity_provider = SessionIdentityProvider(
            session_store=session_store,
            trusted_origins=frozenset(settings.cors_origins),
        )
        app.dependency_overrides[require_authenticated_session] = (
            session_identity_provider.current_session
        )

        def get_injected_session_store() -> SessionStore:
            return session_store

        app.dependency_overrides[require_session_store] = get_injected_session_store
        if dependencies.identity_provider is None:
            app.dependency_overrides[require_authenticated_principal] = (
                session_identity_provider.current_principal
            )
    if dependencies.identity_provider is not None:
        app.dependency_overrides[require_authenticated_principal] = (
            dependencies.identity_provider.current_principal
        )
    if (profile_service := dependencies.profile_service) is not None:
        def get_injected_profile_service() -> ProfileService:
            return profile_service

        app.dependency_overrides[require_profile_service] = get_injected_profile_service
    if (preferences_service := dependencies.preferences_service) is not None:
        def get_injected_preferences_service() -> PreferencesService:
            return preferences_service

        app.dependency_overrides[require_preferences_service] = get_injected_preferences_service
    if (goal_service := dependencies.goal_service) is not None:
        def get_injected_goal_service() -> GoalService:
            return goal_service

        app.dependency_overrides[require_goal_service] = get_injected_goal_service
    if (product_queries := dependencies.product_queries) is not None:
        def get_injected_product_queries() -> ProductQueries:
            return product_queries

        app.dependency_overrides[require_product_queries] = get_injected_product_queries
    if (source_catalog := dependencies.source_catalog) is not None:
        def get_injected_source_catalog() -> CatalogQueries:
            return source_catalog

        app.dependency_overrides[require_source_catalog] = get_injected_source_catalog
    if (editorial_drafts := dependencies.editorial_drafts) is not None:
        def get_injected_editorial_drafts() -> DraftReadService:
            return editorial_drafts

        app.dependency_overrides[require_editorial_drafts] = get_injected_editorial_drafts
    if (m5_queries := dependencies.m5_queries) is not None:
        def get_injected_m5_queries() -> M5Queries:
            return m5_queries

        app.dependency_overrides[require_m5_queries] = get_injected_m5_queries
    if (neo4j_catalog := dependencies.neo4j_catalog) is not None:
        def get_injected_neo4j_catalog() -> Neo4jCatalogQueries:
            return neo4j_catalog

        app.dependency_overrides[require_neo4j_catalog_queries] = get_injected_neo4j_catalog
    if (roadmap_service := dependencies.roadmap_service) is not None:
        def get_injected_roadmap_service() -> RoadmapService:
            return roadmap_service

        app.dependency_overrides[require_roadmap_service] = get_injected_roadmap_service
    if (analysis_service := dependencies.analysis_service) is not None:
        def get_injected_analysis_service() -> AnalysisService:
            return analysis_service

        app.dependency_overrides[require_analysis_service] = get_injected_analysis_service
    if (capability_service := dependencies.capability_service) is not None:
        def get_injected_capability_service() -> CapabilityService:
            return capability_service

        app.dependency_overrides[require_capability_service] = get_injected_capability_service
    if (idempotency_store := dependencies.idempotency_store) is not None:
        def get_injected_idempotency_store() -> IdempotencyStore:
            return idempotency_store

        app.dependency_overrides[require_idempotency_store] = get_injected_idempotency_store

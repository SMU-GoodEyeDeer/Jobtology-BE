from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from jobtology_be.api.analyses import require_analysis_service
from jobtology_be.api.auth_session import require_authenticated_session, require_session_store
from jobtology_be.api.capabilities import require_capability_service
from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.errors import ErrorResponse, register_error_handlers
from jobtology_be.api.goals import require_goal_service
from jobtology_be.api.guide import router as guide_router
from jobtology_be.api.idempotency import IdempotencyStore, require_idempotency_store
from jobtology_be.api.identity import SessionIdentityProvider, require_authenticated_principal
from jobtology_be.api.m5_queries import require_m5_queries
from jobtology_be.api.neo4j_catalog import (
    Neo4jCatalogQueries,
    require_neo4j_catalog_queries,
)
from jobtology_be.api.neo4j_catalog import router as neo4j_catalog_router
from jobtology_be.api.preferences import require_preferences_service
from jobtology_be.api.product_queries import require_product_queries
from jobtology_be.api.profiles import require_profile_service
from jobtology_be.api.roadmaps import require_roadmap_service
from jobtology_be.api.router import router
from jobtology_be.application.m5_queries import M5Queries
from jobtology_be.application.queries import ProductQueries
from jobtology_be.application.services.analyses import AnalysisService, PersistentAnalysisService
from jobtology_be.application.services.analysis_context import (
    ContextSnapshotConfiguration,
    SnapshotBackedAnalysisContextFactory,
)
from jobtology_be.application.services.analysis_inputs import PostgresAnalysisContextInputSource
from jobtology_be.application.services.capabilities import (
    CapabilityService,
    PersistentCapabilityService,
)
from jobtology_be.application.services.goals import GoalService, PersistentGoalService
from jobtology_be.application.services.preferences import (
    PersistentPreferencesService,
    PreferencesService,
)
from jobtology_be.application.services.profiles import PersistentProfileService, ProfileService
from jobtology_be.application.services.roadmaps import PersistentRoadmapService, RoadmapService
from jobtology_be.corpus.local_snapshot import LocalJsonPublishedCorpusSnapshotReader
from jobtology_be.corpus.source_factory import (
    ConfiguredCorpusSource,
    build_configured_corpus_source,
)
from jobtology_be.infrastructure.persistence.auth_store import PostgresAuthStore
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.m5_queries import PostgresM5Queries
from jobtology_be.infrastructure.persistence.preference_queries import PostgresRoutePreferencesQuery
from jobtology_be.infrastructure.persistence.queries import PostgresProductQueries
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.modules.analyses.editorial_models import ReleaseState
from jobtology_be.modules.auth.session import SessionStore
from jobtology_be.modules.auth.session_cookies import (
    SessionCookiePolicy,
    SessionCookiePolicyMiddleware,
)
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
    {
        "name": "native catalog",
        "description": "Neo4j 원본 카탈로그의 안전한 읽기 전용 필드를 조회합니다.",
    },
    {"name": "roadmaps", "description": "저장된 로드맵과 단계 상태를 관리합니다."},
    {
        "name": "development fixtures",
        "description": "명시적으로 활성화한 개발용 읽기 전용 예시입니다. 제품 데이터가 아닙니다.",
    },
    {
        "name": "FE mock samples",
        "description": "명시적으로 활성화한 프론트엔드 mock 전용 읽기 전용 DTO 예시입니다.",
    },
    {"name": "product", "description": "인증된 Jobtology 제품 API입니다."},
]


@asynccontextmanager
async def lifespan(
    _: FastAPI,
    database: Database | None,
    corpus_source: ConfiguredCorpusSource | None,
) -> AsyncIterator[None]:
    try:
        yield
    finally:
        try:
            if corpus_source is not None:
                await corpus_source.aclose()
        finally:
            if database is not None:
                await database.dispose()


def database_lifespan(
    database: Database | None,
    corpus_source: ConfiguredCorpusSource | None = None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    return lambda app: lifespan(app, database, corpus_source)


def create_app(
    settings: Settings | None = None,
    *,
    dependencies: ApiDependencies | None = None,
) -> FastAPI:
    settings = settings or Settings()
    dependencies = dependencies or ApiDependencies()
    profile_service = dependencies.profile_service
    goal_service = dependencies.goal_service
    roadmap_service = dependencies.roadmap_service
    analysis_service = (
        dependencies.analysis_service if settings.corpus_source == "local_json" else None
    )
    capability_service = dependencies.capability_service
    preferences_service = dependencies.preferences_service
    idempotency_store = dependencies.idempotency_store
    analysis_recompute_submitter = dependencies.analysis_recompute_submitter
    analysis_context_factory = dependencies.analysis_context_factory
    database = None
    corpus_source = None
    store = None
    session_store = dependencies.session_store
    m5_queries = dependencies.m5_queries
    neo4j_catalog = dependencies.neo4j_catalog
    product_queries = dependencies.product_queries
    if settings.corpus_source == "neo4j_query_api":
        corpus_source = build_configured_corpus_source(settings)
    if settings.database_url is not None:
        database = Database.create(settings.database_url)
        store = PostgresApplicationStore(database)
        idempotency_store = idempotency_store or store
        corpus_source = corpus_source or build_configured_corpus_source(settings)
        snapshot_reader = corpus_source.local_snapshot_reader
        m5_queries = m5_queries or PostgresM5Queries(database, snapshot_reader)
        product_queries = product_queries or PostgresProductQueries(database)
        preferences_service = preferences_service or PersistentPreferencesService(
            store, PostgresRoutePreferencesQuery(database)
        )
        analysis_recompute_submitter = analysis_recompute_submitter or store
        analysis_context_factory = analysis_context_factory or _configured_analysis_context_factory(
            database, snapshot_reader
        )
    if corpus_source is not None and neo4j_catalog is None:
        neo4j_catalog = corpus_source.native_catalog
    if settings.auth_enabled and session_store is None:
        if database is None:
            raise ValueError("Enabled authentication requires a database URL or injected session store")
        session_store = PostgresAuthStore(database)
    if profile_service is None and store is not None:
        profile_service = PersistentProfileService(store)
    if goal_service is None and store is not None:
        goal_service = PersistentGoalService(store)
    if roadmap_service is None and store is not None:
        roadmap_service = PersistentRoadmapService(store)
    if capability_service is None and store is not None:
        capability_service = PersistentCapabilityService(store)
    if (
        settings.corpus_source == "local_json"
        and analysis_service is None
        and analysis_context_factory is not None
        and analysis_recompute_submitter is not None
    ):
        analysis_service = PersistentAnalysisService(
            analysis_context_factory,
            analysis_recompute_submitter,
        )
    app = FastAPI(
        title="Jobtology API",
        summary="개인화 역량 분석과 로드맵을 위한 Jobtology API",
        description=(
            "제품 API는 `/api/v1` 경로군에 적용됩니다. `/api/v2`는 명시적으로 구성된 "
            "Neo4j 원본 카탈로그의 읽기 전용 계약입니다. 모든 제품 API는 인증된 세션을 요구하며, "
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
        lifespan=database_lifespan(database, corpus_source),
    )
    register_error_handlers(app)
    app.add_middleware(
        SessionCookiePolicyMiddleware,
        policy=SessionCookiePolicy(
            secure=settings.session_cookie_secure,
            session_ttl_seconds=settings.session_ttl_seconds,
        ),
    )
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
    if profile_service is not None:
        def get_injected_profile_service() -> ProfileService:
            return profile_service

        app.dependency_overrides[require_profile_service] = get_injected_profile_service
    if preferences_service is not None:
        def get_injected_preferences_service() -> PreferencesService:
            return preferences_service

        app.dependency_overrides[require_preferences_service] = get_injected_preferences_service
    if goal_service is not None:
        def get_injected_goal_service() -> GoalService:
            return goal_service

        app.dependency_overrides[require_goal_service] = get_injected_goal_service
    if product_queries is not None:
        def get_injected_product_queries() -> ProductQueries:
            return product_queries

        app.dependency_overrides[require_product_queries] = get_injected_product_queries
    if m5_queries is not None:
        def get_injected_m5_queries() -> M5Queries:
            return m5_queries

        app.dependency_overrides[require_m5_queries] = get_injected_m5_queries
    if neo4j_catalog is not None:
        def get_injected_neo4j_catalog() -> Neo4jCatalogQueries:
            return neo4j_catalog

        app.dependency_overrides[require_neo4j_catalog_queries] = get_injected_neo4j_catalog
    if roadmap_service is not None:
        def get_injected_roadmap_service() -> RoadmapService:
            return roadmap_service

        app.dependency_overrides[require_roadmap_service] = get_injected_roadmap_service
    if analysis_service is not None:
        def get_injected_analysis_service() -> AnalysisService:
            return analysis_service

        app.dependency_overrides[require_analysis_service] = get_injected_analysis_service
    if capability_service is not None:
        def get_injected_capability_service() -> CapabilityService:
            return capability_service

        app.dependency_overrides[require_capability_service] = get_injected_capability_service
    if idempotency_store is not None:
        def get_injected_idempotency_store() -> IdempotencyStore:
            return idempotency_store

        app.dependency_overrides[require_idempotency_store] = get_injected_idempotency_store
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
    )
    app.include_router(router, prefix="/api/v1")
    app.include_router(neo4j_catalog_router, prefix="/api/v2")
    app.include_router(guide_router)
    if settings.enable_fixtures:
        from jobtology_be.api.fixtures import router as fixtures_router

        app.include_router(fixtures_router, prefix="/api/v1/dev")
    if settings.enable_fe_mock_samples:
        from jobtology_be.api.fe_mock_samples import router as fe_mock_samples_router

        app.include_router(fe_mock_samples_router, prefix="/api/v1/dev/mock")
    return app


def _configured_analysis_context_factory(
    database: Database,
    snapshot_reader: LocalJsonPublishedCorpusSnapshotReader | None,
) -> SnapshotBackedAnalysisContextFactory | None:
    if snapshot_reader is None:
        return None
    selections = frozenset(
        (snapshot.basis_version, snapshot.release.release_id)
        for snapshot in snapshot_reader.snapshots
        if snapshot.release.state is ReleaseState.PUBLISHED
        and not snapshot.is_fixture
        and snapshot.release.release_id is not None
    )
    if len(selections) != 1:
        return None
    basis_version, release_id = next(iter(selections))
    return SnapshotBackedAnalysisContextFactory(
        source=PostgresAnalysisContextInputSource(database),
        snapshot_reader=snapshot_reader,
        configuration=ContextSnapshotConfiguration(
            basis_version=basis_version,
            release_id=release_id,
        ),
    )


app = create_app()

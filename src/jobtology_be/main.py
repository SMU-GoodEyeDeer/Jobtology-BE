from dataclasses import replace
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from jobtology_be.api.composition import (
    configured_analysis_context_factory as _configured_analysis_context_factory,
)
from jobtology_be.api.composition import database_lifespan, register_api_dependencies
from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.editorial_drafts import router as editorial_drafts_router
from jobtology_be.api.errors import register_error_handlers
from jobtology_be.api.google_auth import (
    require_google_login_store,
    require_google_provider,
    require_google_settings,
)
from jobtology_be.api.google_auth import (
    router as google_auth_router,
)
from jobtology_be.api.guide import router as guide_router
from jobtology_be.api.live_source import router as live_source_router
from jobtology_be.api.neo4j_catalog import router as neo4j_catalog_router
from jobtology_be.api.router import create_api_shell, router
from jobtology_be.api.source_catalog import router as source_catalog_router
from jobtology_be.application.services.analyses import PersistentAnalysisService
from jobtology_be.application.services.capabilities import PersistentCapabilityService
from jobtology_be.application.services.goals import PersistentGoalService
from jobtology_be.application.services.preferences import PersistentPreferencesService
from jobtology_be.application.services.profiles import PersistentProfileService
from jobtology_be.application.services.roadmaps import PersistentRoadmapService
from jobtology_be.corpus.source_factory import build_configured_corpus_source
from jobtology_be.editorial.reader import DraftReadService, load_drafts
from jobtology_be.infrastructure.persistence.auth_store import PostgresAuthStore
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.live_source_feed import PostgresLiveSourceFeed
from jobtology_be.infrastructure.persistence.m5_queries import PostgresM5Queries
from jobtology_be.infrastructure.persistence.preference_queries import PostgresRoutePreferencesQuery
from jobtology_be.infrastructure.persistence.queries import PostgresProductQueries
from jobtology_be.infrastructure.persistence.source_catalog import PostgresSourceCatalog
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.modules.auth.google_oidc import GoogleOidcProvider
from jobtology_be.modules.auth.session_cookies import (
    OAuthCallbackQueryMiddleware,
    SessionCookiePolicy,
    SessionCookiePolicyMiddleware,
)
from jobtology_be.product_roles.checklist import (
    OnboardingChecklistCatalog,
    load_checklist_document,
)
from jobtology_be.product_roles.context import ProductRoleAnalysisContextFactory
from jobtology_be.product_roles.holder import ProductRoleHolder
from jobtology_be.product_roles.worker import InProcessRecomputeLoop
from jobtology_be.settings import Settings
from jobtology_be.workers.recompute import build_leased_recompute_worker


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
    source_catalog = dependencies.source_catalog
    live_source_feed = dependencies.live_source_feed
    source_catalog_resource = None
    if settings.catalog_database_url is not None and source_catalog is None:
        source_catalog_resource = PostgresSourceCatalog.create(
            settings.catalog_database_url.get_secret_value()
        )
        source_catalog = source_catalog_resource
    if live_source_feed is None and isinstance(source_catalog, PostgresSourceCatalog):
        live_source_feed = PostgresLiveSourceFeed(engine=source_catalog.engine)
    policy_resource = files("jobtology_be.product_roles").joinpath("policy.v1.json")
    policy_path = (policy_resource if policy_resource.is_file() else
                   Path(__file__).resolve().parents[2] / "config/product_roles/policy.v1.json")
    role_holder = (
        ProductRoleHolder(
            source_catalog.engine,
            policy_path,
            settings.product_role_artifact_approval_path,
        )
        if settings.product_roles_enabled and settings.catalog_database_url is not None
        and isinstance(source_catalog, PostgresSourceCatalog)
        else None
    )
    onboarding_checklist = dependencies.onboarding_checklist
    if onboarding_checklist is None and role_holder is not None:
        checklist_resource = files("jobtology_be.product_roles").joinpath(
            "onboarding_checklist.v1.json"
        )
        onboarding_checklist = OnboardingChecklistCatalog(
            document=load_checklist_document(
                checklist_resource if checklist_resource.is_file() else
                Path(__file__).resolve().parents[2]
                / "config/product_roles/onboarding_checklist.v1.json"
            ),
            snapshots=lambda: role_holder.snapshots,
        )
    if settings.product_roles_enabled and role_holder is None:
        analysis_service = None
        analysis_context_factory = None
    editorial_drafts = dependencies.editorial_drafts
    if settings.editorial_draft_path is not None and editorial_drafts is None:
        editorial_drafts = DraftReadService(load_drafts(settings.editorial_draft_path))
    if settings.corpus_source == "neo4j_query_api":
        corpus_source = build_configured_corpus_source(settings)
    if settings.database_url is not None:
        database = Database.create(settings.database_url)
        store = PostgresApplicationStore(
            database, capability_list_authoritative=settings.capability_list_authoritative
        )
        idempotency_store = idempotency_store or store
        corpus_source = corpus_source or build_configured_corpus_source(settings)
        snapshot_reader = (role_holder if role_holder is not None else
                           None if settings.product_roles_enabled else corpus_source.local_snapshot_reader)
        m5_queries = m5_queries or PostgresM5Queries(
            database, snapshot_reader=snapshot_reader,
        )
        product_queries = product_queries or PostgresProductQueries(database)
        preferences_service = preferences_service or PersistentPreferencesService(
            store, PostgresRoutePreferencesQuery(database)
        )
        analysis_recompute_submitter = analysis_recompute_submitter or store
        if not settings.product_roles_enabled:
            analysis_context_factory = analysis_context_factory or _configured_analysis_context_factory(
                database, corpus_source.local_snapshot_reader, settings.capability_list_authoritative,
            )
        if role_holder is not None:
            analysis_context_factory = ProductRoleAnalysisContextFactory(
                database, role_holder,
                capability_list_authoritative=settings.capability_list_authoritative,
            )
    if corpus_source is not None and neo4j_catalog is None:
        neo4j_catalog = corpus_source.native_catalog
    if (settings.auth_enabled or settings.guest_sessions_enabled) and session_store is None:
        if database is None:
            raise ValueError("Enabled authentication requires a database URL or injected session store")
        session_store = PostgresAuthStore(database)
    google_login_store = dependencies.google_login_store
    if settings.auth_enabled and google_login_store is None and isinstance(session_store, PostgresAuthStore):
        google_login_store = session_store
    google_provider = dependencies.google_identity_provider
    if settings.auth_enabled and google_provider is None:
        google_provider = GoogleOidcProvider(settings.google_oidc_settings)
    if profile_service is None and store is not None:
        profile_service = PersistentProfileService(store)
    if goal_service is None and store is not None:
        goal_service = PersistentGoalService(store)
    if roadmap_service is None and store is not None:
        roadmap_service = PersistentRoadmapService(store)
    if capability_service is None and store is not None:
        capability_service = PersistentCapabilityService(store)
    if (
        (settings.corpus_source == "local_json" or role_holder is not None)
        and analysis_service is None
        and analysis_context_factory is not None
        and analysis_recompute_submitter is not None
    ):
        analysis_service = PersistentAnalysisService(
            analysis_context_factory,
            analysis_recompute_submitter,
        )
    inprocess_worker = None
    if (settings.inprocess_worker_enabled and store is not None
            and database is not None and role_holder is not None):
        inprocess_worker = InProcessRecomputeLoop(
            worker=build_leased_recompute_worker(
                store=store,
                snapshot_reader=role_holder,
                eligible_corpus_sources=frozenset({"local_json"}),
            ),
            database=database,
            roadmap_service=roadmap_service or PersistentRoadmapService(store),
            holder=role_holder,
        )
    app = create_api_shell(
        database_lifespan(
            database, corpus_source, source_catalog_resource, role_holder, inprocess_worker,
        ),
    )
    register_error_handlers(app)
    app.add_middleware(OAuthCallbackQueryMiddleware)
    app.dependency_overrides[require_google_settings] = lambda: settings
    app.dependency_overrides[require_google_login_store] = lambda: google_login_store
    app.dependency_overrides[require_google_provider] = lambda: google_provider
    app.add_middleware(
        SessionCookiePolicyMiddleware,
        policy=SessionCookiePolicy(
            secure=settings.session_cookie_secure,
            session_ttl_seconds=settings.session_ttl_seconds,
        ),
    )
    register_api_dependencies(
        app,
        replace(
            dependencies,
            profile_service=profile_service,
            preferences_service=preferences_service,
            goal_service=goal_service,
            product_queries=product_queries,
            source_catalog=source_catalog,
            live_source_feed=live_source_feed,
            requirement_metadata=role_holder or dependencies.requirement_metadata,
            occupation_display_names=role_holder or dependencies.occupation_display_names,
            editorial_drafts=editorial_drafts,
            m5_queries=m5_queries,
            neo4j_catalog=neo4j_catalog,
            roadmap_service=roadmap_service,
            analysis_service=analysis_service,
            capability_service=capability_service,
            onboarding_checklist=onboarding_checklist,
            idempotency_store=idempotency_store,
            session_store=session_store,
        ),
        settings,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
    )
    app.include_router(router, prefix="/api/v1")
    app.include_router(google_auth_router, prefix="/api/v1")
    app.include_router(editorial_drafts_router, prefix="/api/v1")
    app.include_router(neo4j_catalog_router, prefix="/api/v2")
    app.include_router(source_catalog_router, prefix="/api/v2")
    app.include_router(live_source_router, prefix="/api/v2")
    app.include_router(guide_router)
    if settings.enable_fixtures:
        from jobtology_be.api.fixtures import router as fixtures_router

        app.include_router(fixtures_router, prefix="/api/v1/dev")
    if settings.enable_fe_mock_samples:
        from jobtology_be.api.fe_mock_samples import router as fe_mock_samples_router

        app.include_router(fe_mock_samples_router, prefix="/api/v1/dev/mock")
    return app


app = create_app()

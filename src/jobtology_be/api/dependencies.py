from dataclasses import dataclass

from jobtology_be.api.idempotency import IdempotencyStore
from jobtology_be.api.identity import IdentityProvider
from jobtology_be.api.neo4j_catalog import Neo4jCatalogQueries
from jobtology_be.application.m5_queries import M5Queries
from jobtology_be.application.queries import ProductQueries
from jobtology_be.application.services.analyses import (
    AnalysisContextFactory,
    AnalysisRecomputeSubmitter,
    AnalysisService,
)
from jobtology_be.application.services.capabilities import CapabilityService
from jobtology_be.application.services.goals import GoalService
from jobtology_be.application.services.preferences import PreferencesService
from jobtology_be.application.services.profiles import ProfileService
from jobtology_be.application.services.roadmaps import RoadmapService
from jobtology_be.modules.auth.session import SessionStore


@dataclass(frozen=True, slots=True)
class ApiDependencies:
    identity_provider: IdentityProvider | None = None
    session_store: SessionStore | None = None
    profile_service: ProfileService | None = None
    preferences_service: PreferencesService | None = None
    goal_service: GoalService | None = None
    m5_queries: M5Queries | None = None
    neo4j_catalog: Neo4jCatalogQueries | None = None
    product_queries: ProductQueries | None = None
    roadmap_service: RoadmapService | None = None
    analysis_service: AnalysisService | None = None
    analysis_context_factory: AnalysisContextFactory | None = None
    analysis_recompute_submitter: AnalysisRecomputeSubmitter | None = None
    capability_service: CapabilityService | None = None
    idempotency_store: IdempotencyStore | None = None

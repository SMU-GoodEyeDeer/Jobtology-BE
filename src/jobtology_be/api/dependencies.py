from dataclasses import dataclass

from jobtology_be.api.google_auth import GoogleLoginStore
from jobtology_be.api.idempotency import IdempotencyStore
from jobtology_be.api.identity import IdentityProvider
from jobtology_be.api.neo4j_catalog import Neo4jCatalogQueries
from jobtology_be.application.m5_queries import M5Queries
from jobtology_be.application.queries import ProductQueries
from jobtology_be.application.requirement_metadata import (
    OccupationDisplayNames,
    RequirementMetadataLookup,
)
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
from jobtology_be.editorial.reader import DraftReadService
from jobtology_be.infrastructure.persistence.live_source_feed import LiveSourceFeed
from jobtology_be.infrastructure.persistence.source_catalog import CatalogQueries
from jobtology_be.modules.auth.google_oidc import GoogleIdentityProvider
from jobtology_be.modules.auth.session import SessionStore
from jobtology_be.product_roles.checklist import OnboardingChecklistCatalog


@dataclass(frozen=True, slots=True)
class ApiDependencies:
    identity_provider: IdentityProvider | None = None
    session_store: SessionStore | None = None
    google_login_store: GoogleLoginStore | None = None
    google_identity_provider: GoogleIdentityProvider | None = None
    profile_service: ProfileService | None = None
    preferences_service: PreferencesService | None = None
    goal_service: GoalService | None = None
    m5_queries: M5Queries | None = None
    neo4j_catalog: Neo4jCatalogQueries | None = None
    product_queries: ProductQueries | None = None
    source_catalog: CatalogQueries | None = None
    live_source_feed: LiveSourceFeed | None = None
    requirement_metadata: RequirementMetadataLookup | None = None
    occupation_display_names: OccupationDisplayNames | None = None
    editorial_drafts: DraftReadService | None = None
    roadmap_service: RoadmapService | None = None
    analysis_service: AnalysisService | None = None
    analysis_context_factory: AnalysisContextFactory | None = None
    analysis_recompute_submitter: AnalysisRecomputeSubmitter | None = None
    capability_service: CapabilityService | None = None
    onboarding_checklist: OnboardingChecklistCatalog | None = None
    idempotency_store: IdempotencyStore | None = None

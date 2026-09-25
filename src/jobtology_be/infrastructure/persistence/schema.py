"""Application-owned PostgreSQL schema metadata."""

from sqlalchemy import (
    BIGINT,
    BOOLEAN,
    DATE,
    INTEGER,
    TEXT,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

METADATA = MetaData()
UTC_NOW = text("CURRENT_TIMESTAMP")

users = Table(
    "users",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("status", String(32), nullable=False, server_default=text("'ACTIVE'")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    CheckConstraint("status IN ('ACTIVE', 'DEACTIVATED')", name="users_status_check"),
)

profiles = Table(
    "profiles",
    METADATA,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True),
    Column("version", BIGINT, nullable=False, server_default=text("1")),
    Column("major_raw", TEXT, nullable=False, server_default=text("''")),
    Column("major_concept_id", String(128)),
    Column("year", INTEGER),
    Column("enrollment_status", String(32)),
    Column("expected_graduation_on", DATE),
    Column("latest_analysis_id", UUID(as_uuid=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    CheckConstraint("version >= 1", name="profiles_version_check"),
)

google_identities = Table(
    "google_identities",
    METADATA,
    Column("issuer", String(255), primary_key=True),
    Column("subject", String(255), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    CheckConstraint(
        "issuer = 'https://accounts.google.com'", name="google_identities_issuer_check"
    ),
    CheckConstraint("char_length(subject) > 0", name="google_identities_subject_check"),
)

oauth_login_attempts = Table(
    "oauth_login_attempts",
    METADATA,
    Column("state_hash", LargeBinary(32), primary_key=True),
    Column("browser_binding_hash", LargeBinary(32), nullable=False),
    Column("nonce", TEXT, nullable=False),
    Column("pkce_verifier", TEXT, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    CheckConstraint("octet_length(state_hash) = 32", name="oauth_login_attempts_state_hash_check"),
    CheckConstraint(
        "octet_length(browser_binding_hash) = 32",
        name="oauth_login_attempts_browser_binding_hash_check",
    ),
)
Index("oauth_login_attempts_expiry_index", oauth_login_attempts.c.expires_at)

auth_sessions = Table(
    "auth_sessions",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("token_hash", LargeBinary(32), nullable=False, unique=True),
    Column("csrf_hash", LargeBinary(32), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    CheckConstraint("octet_length(token_hash) = 32", name="auth_sessions_token_hash_check"),
    CheckConstraint("octet_length(csrf_hash) = 32", name="auth_sessions_csrf_hash_check"),
)
Index("auth_sessions_active_lookup_index", auth_sessions.c.token_hash, auth_sessions.c.expires_at)

user_state_events = Table(
    "user_state_events",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("aggregate_id", UUID(as_uuid=True), nullable=False),
    Column("aggregate_type", String(64), nullable=False),
    Column("version", BIGINT, nullable=False),
    Column("kind", String(128), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    UniqueConstraint("aggregate_id", "version", name="user_state_events_aggregate_version_key"),
    CheckConstraint("version >= 1", name="user_state_events_version_check"),
)

user_capabilities = Table(
    "user_capabilities",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("category", String(64), nullable=False),
    Column("raw_text", TEXT, nullable=False),
    Column("entity_id", String(128)),
    Column("proficiency", String(32)),
    Column("verification", String(32), nullable=False, server_default=text("'SELF_REPORTED'")),
    Column("lifecycle", String(32), nullable=False, server_default=text("'ACTIVE'")),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("source_completion_event_id", UUID(as_uuid=True), ForeignKey("user_state_events.id")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    UniqueConstraint(
        "source_completion_event_id",
        "entity_id",
        name="user_capabilities_completion_entity_key",
    ),
    CheckConstraint("char_length(trim(raw_text)) > 0", name="user_capabilities_raw_text_check"),
)
Index(
    "user_capabilities_user_lifecycle_index",
    user_capabilities.c.user_id,
    user_capabilities.c.lifecycle,
)

goals = Table(
    "goals",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("goal_mode", String(16), nullable=False),
    Column("occupation_id", String(128)),
    Column("target_by", DateTime(timezone=True), nullable=False),
    Column("timezone", String(100), nullable=False),
    Column("original_time_phrase", String(500), nullable=False),
    Column("status", String(16), nullable=False, server_default=text("'DRAFT'")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    CheckConstraint("goal_mode IN ('TARGETED', 'DISCOVERY')", name="goals_mode_check"),
    CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')", name="goals_status_check"),
    CheckConstraint(
        "goal_mode = 'DISCOVERY' OR occupation_id IS NOT NULL",
        name="goals_targeted_occupation_check",
    ),
)
Index(
    "goals_one_active_per_user",
    goals.c.user_id,
    unique=True,
    postgresql_where=goals.c.status == "ACTIVE",
)

route_preferences = Table(
    "route_preferences",
    METADATA,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True),
    Column("available_hours_per_week", INTEGER, nullable=False),
    Column("availability_source", String(64), nullable=False),
    Column("budget_mode", String(16), nullable=False),
    Column("max_out_of_pocket_krw", BIGINT),
    Column("fastest_path", BOOLEAN, nullable=False, server_default=text("false")),
    Column("needs_portfolio", BOOLEAN, nullable=False, server_default=text("false")),
    Column("career_switch", BOOLEAN, nullable=False, server_default=text("false")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    CheckConstraint(
        "available_hours_per_week BETWEEN 1 AND 60", name="route_preferences_capacity_check"
    ),
    CheckConstraint(
        "max_out_of_pocket_krw IS NULL OR max_out_of_pocket_krw >= 0",
        name="route_preferences_budget_check",
    ),
)

analyses = Table(
    "analyses",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("goal_id", UUID(as_uuid=True), ForeignKey("goals.id"), nullable=False),
    Column("profile_version", BIGINT, nullable=False),
    Column("basis_type", String(16), nullable=False),
    Column("basis_version", String(128), nullable=False),
    Column("release_id", String(128)),
    Column("methodology_version", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("reference_at", DateTime(timezone=True), nullable=False),
    Column("input_snapshot", JSONB, nullable=False),
    Column("input_hash", String(128), nullable=False),
    Column("results", JSONB),
    Column("is_fixture", BOOLEAN, nullable=False, server_default=text("false")),
    Column("generated_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    CheckConstraint("profile_version >= 1", name="analyses_profile_version_check"),
    CheckConstraint("basis_type IN ('EDITORIAL', 'MARKET')", name="analyses_basis_type_check"),
)
Index("analyses_user_generated_index", analyses.c.user_id, analyses.c.generated_at.desc())

calculation_traces = Table(
    "calculation_traces",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("input_hash", String(128), nullable=False),
    Column("versions", JSONB, nullable=False),
    Column("release_id", String(128)),
    Column("outputs", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
)

route_proposals = Table(
    "route_proposals",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("analysis_id", UUID(as_uuid=True), ForeignKey("analyses.id"), nullable=False),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("proposal_hash", String(128), nullable=False),
    Column("profile_version", BIGINT, nullable=False),
    Column("constraints_snapshot", JSONB, nullable=False),
    Column("feasibility", String(16), nullable=False),
    Column("optimization_status", String(32), nullable=False),
    Column("steps", JSONB, nullable=False),
    Column(
        "decision_trace_id", UUID(as_uuid=True), ForeignKey("calculation_traces.id"), nullable=False
    ),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    CheckConstraint("profile_version >= 1", name="route_proposals_profile_version_check"),
    CheckConstraint(
        "feasibility IN ('FEASIBLE', 'RISKY', 'INFEASIBLE')",
        name="route_proposals_feasibility_check",
    ),
)

roadmaps = Table(
    "roadmaps",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("goal_id", UUID(as_uuid=True), ForeignKey("goals.id"), nullable=False),
    Column("proposal_id", UUID(as_uuid=True), ForeignKey("route_proposals.id"), nullable=False),
    Column("title", String(200), nullable=False),
    Column("state", String(16), nullable=False, server_default=text("'DRAFT'")),
    Column("version", BIGINT, nullable=False, server_default=text("1")),
    Column("profile_version", BIGINT, nullable=False),
    Column("release_id", String(128)),
    Column("validity", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    CheckConstraint("state IN ('DRAFT', 'ACTIVE', 'ARCHIVED')", name="roadmaps_state_check"),
    CheckConstraint("version >= 1", name="roadmaps_version_check"),
)
Index(
    "roadmaps_one_active_per_user",
    roadmaps.c.user_id,
    unique=True,
    postgresql_where=roadmaps.c.state == "ACTIVE",
)

roadmap_steps = Table(
    "roadmap_steps",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("roadmap_id", UUID(as_uuid=True), ForeignKey("roadmaps.id"), nullable=False),
    Column("step_key", String(128), nullable=False),
    Column("position", INTEGER, nullable=False),
    Column("action_id", String(128), nullable=False),
    Column("template_revision", INTEGER, nullable=False),
    Column("state", String(16), nullable=False, server_default=text("'TODO'")),
    Column("planned_start", DateTime(timezone=True)),
    Column("planned_end", DateTime(timezone=True)),
    Column("outcomes", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("criteria", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    UniqueConstraint("roadmap_id", "step_key", name="roadmap_steps_roadmap_step_key_key"),
    UniqueConstraint("roadmap_id", "position", name="roadmap_steps_roadmap_position_key"),
    UniqueConstraint("id", "roadmap_id", name="roadmap_steps_id_roadmap_key"),
    CheckConstraint("position >= 0", name="roadmap_steps_position_check"),
    CheckConstraint("template_revision >= 1", name="roadmap_steps_template_revision_check"),
    CheckConstraint(
        "state IN ('TODO', 'IN_PROGRESS', 'COMPLETED')", name="roadmap_steps_state_check"
    ),
)

step_dependencies = Table(
    "step_dependencies",
    METADATA,
    Column("roadmap_id", UUID(as_uuid=True), nullable=False),
    Column("step_id", UUID(as_uuid=True), nullable=False),
    Column("prerequisite_step_id", UUID(as_uuid=True), nullable=False),
    PrimaryKeyConstraint("roadmap_id", "step_id", "prerequisite_step_id"),
    ForeignKeyConstraint(["roadmap_id"], ["roadmaps.id"]),
    ForeignKeyConstraint(
        ["roadmap_id", "step_id"], ["roadmap_steps.roadmap_id", "roadmap_steps.id"]
    ),
    ForeignKeyConstraint(
        ["roadmap_id", "prerequisite_step_id"], ["roadmap_steps.roadmap_id", "roadmap_steps.id"]
    ),
    CheckConstraint("step_id <> prerequisite_step_id", name="step_dependencies_no_self_edge_check"),
)

step_completion_inheritances = Table(
    "step_completion_inheritances",
    METADATA,
    Column("step_id", UUID(as_uuid=True), ForeignKey("roadmap_steps.id"), primary_key=True),
    Column(
        "original_completion_event_id",
        UUID(as_uuid=True),
        ForeignKey("user_state_events.id"),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
)

recompute_requests = Table(
    "recompute_requests",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("profile_version", BIGINT, nullable=False),
    Column(
        "trigger_event_id", UUID(as_uuid=True), ForeignKey("user_state_events.id"), nullable=False
    ),
    Column("state", String(16), nullable=False, server_default=text("'PENDING'")),
    Column("resulting_analysis_id", UUID(as_uuid=True), ForeignKey("analyses.id")),
    Column("proposal_id", UUID(as_uuid=True), ForeignKey("route_proposals.id")),
    Column("error_code", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    UniqueConstraint(
        "user_id", "profile_version", "trigger_event_id", name="recompute_requests_trigger_key"
    ),
)

recompute_contexts = Table(
    "recompute_contexts",
    METADATA,
    Column(
        "request_id",
        UUID(as_uuid=True),
        ForeignKey("recompute_requests.id"),
        primary_key=True,
    ),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
)

outbox_jobs = Table(
    "outbox_jobs",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("kind", String(128), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("recompute_request_id", UUID(as_uuid=True), ForeignKey("recompute_requests.id")),
    Column("dedupe_key", String(256), nullable=False, unique=True),
    Column("state", String(16), nullable=False, server_default=text("'READY'")),
    Column("attempt_count", INTEGER, nullable=False, server_default=text("0")),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("lease_until", DateTime(timezone=True)),
    Column("lease_token", UUID(as_uuid=True)),
    Column("last_error", TEXT),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    UniqueConstraint("recompute_request_id", name="outbox_jobs_recompute_request_id_key"),
    CheckConstraint("attempt_count >= 0", name="outbox_jobs_attempt_count_check"),
    CheckConstraint(
        "state IN ('READY', 'LEASED', 'COMPLETED', 'FAILED')", name="outbox_jobs_state_check"
    ),
)
Index("outbox_jobs_claim_index", outbox_jobs.c.state, outbox_jobs.c.available_at)
Index("outbox_jobs_recompute_request_id_index", outbox_jobs.c.recompute_request_id)

idempotency_records = Table(
    "idempotency_records",
    METADATA,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("method", String(16), nullable=False),
    Column("path", String(512), nullable=False),
    Column("key", String(255), nullable=False),
    Column("request_hash", String(128), nullable=False),
    Column("response_status", INTEGER),
    Column("response", JSONB),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    PrimaryKeyConstraint("user_id", "method", "path", "key"),
)
Index("idempotency_records_expiry_index", idempotency_records.c.expires_at)

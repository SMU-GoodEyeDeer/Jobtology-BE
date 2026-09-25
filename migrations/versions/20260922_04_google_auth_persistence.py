from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260922_04"
down_revision: str | Sequence[str] | None = "20260922_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "google_identities",
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "issuer = 'https://accounts.google.com'", name="google_identities_issuer_check"
        ),
        sa.CheckConstraint("char_length(subject) > 0", name="google_identities_subject_check"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("issuer", "subject"),
    )
    op.create_table(
        "oauth_login_attempts",
        sa.Column("state_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("browser_binding_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("pkce_verifier", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "octet_length(browser_binding_hash) = 32",
            name="oauth_login_attempts_browser_binding_hash_check",
        ),
        sa.CheckConstraint(
            "octet_length(state_hash) = 32", name="oauth_login_attempts_state_hash_check"
        ),
        sa.PrimaryKeyConstraint("state_hash"),
    )
    op.create_index(
        "oauth_login_attempts_expiry_index", "oauth_login_attempts", ["expires_at"]
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("csrf_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("octet_length(csrf_hash) = 32", name="auth_sessions_csrf_hash_check"),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="auth_sessions_token_hash_check"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "auth_sessions_active_lookup_index", "auth_sessions", ["token_hash", "expires_at"]
    )


def downgrade() -> None:
    op.drop_index("auth_sessions_active_lookup_index", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("oauth_login_attempts_expiry_index", table_name="oauth_login_attempts")
    op.drop_table("oauth_login_attempts")
    op.drop_table("google_identities")

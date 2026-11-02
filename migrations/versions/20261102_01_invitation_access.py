"""Invitation admission, grandfathered accounts and private management audit."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261102_01"
down_revision = "20261020_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invitation_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code_hash", sa.LargeBinary(32), nullable=False, unique=True),
        sa.Column("code_hint", sa.String(8), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("access_hours", sa.Float),
        sa.Column("max_uses", sa.Integer),
        sa.Column("use_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("octet_length(code_hash) = 32", name="invitation_code_hash_check"),
        sa.CheckConstraint(
            "access_hours IS NULL OR (access_hours > 0 AND access_hours <= 876000)",
            name="invitation_access_hours_check",
        ),
        sa.CheckConstraint("max_uses IS NULL OR max_uses > 0", name="invitation_max_uses_check"),
        sa.CheckConstraint("use_count >= 0", name="invitation_use_count_check"),
    )
    op.create_table(
        "account_access",
        sa.Column("subject_hash", sa.LargeBinary(32), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("invitation_id", UUID(as_uuid=True), sa.ForeignKey("invitation_codes.id")),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "octet_length(subject_hash) = 32", name="account_access_subject_hash_check"
        ),
        sa.CheckConstraint(
            "source IN ('LEGACY', 'INVITATION')", name="account_access_source_check"
        ),
    )
    op.create_table(
        "management_audit",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column("oauth_login_attempts", sa.Column("invitation_hash", sa.LargeBinary(32)))
    # PostgreSQL's built-in bytea SHA-256 requires no pgcrypto extension/grants.
    # Keep the exact issuer + NUL + subject domain separator used at login.
    op.execute("""
        INSERT INTO account_access (subject_hash, user_id, source)
        SELECT sha256(convert_to('https://accounts.google.com','UTF8') ||
                      decode('00','hex') || convert_to(subject,'UTF8')),
               user_id, 'LEGACY'
        FROM google_identities
    """)


def downgrade() -> None:
    op.drop_column("oauth_login_attempts", "invitation_hash")
    for table in ("management_audit", "account_access", "invitation_codes"):
        op.drop_table(table)

"""Persist shared per-account API quotas without retaining per-request history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261019_01"
down_revision: str | Sequence[str] | None = "20261018_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("google_identities_user_id_index", "google_identities", ["user_id"])
    op.create_table(
        "account_rate_limits",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("request_count >= 1", name="account_rate_limits_count_check"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "scope"),
    )


def downgrade() -> None:
    op.drop_table("account_rate_limits")
    op.drop_index("google_identities_user_id_index", table_name="google_identities")

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261018_01"
down_revision: str | Sequence[str] | None = "20261006_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roadmap_plan_overrides",
        sa.Column("roadmap_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("weekly_hours", sa.Integer(), nullable=False),
        sa.Column("rest_weeks", postgresql.JSONB(), nullable=False),
        sa.Column("step_order", postgresql.JSONB(), nullable=False),
        sa.Column("step_hours", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("weekly_hours BETWEEN 1 AND 60",
                           name="roadmap_plan_overrides_capacity_check"),
        sa.ForeignKeyConstraint(["roadmap_id"], ["roadmaps.id"]),
        sa.PrimaryKeyConstraint("roadmap_id"),
    )


def downgrade() -> None:
    op.drop_table("roadmap_plan_overrides")

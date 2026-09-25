from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260922_02"
down_revision: str | Sequence[str] | None = "20260922_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "step_completion_inheritances" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "step_completion_inheritances",
        sa.Column("step_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("original_completion_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["step_id"], ["roadmap_steps.id"]),
        sa.ForeignKeyConstraint(["original_completion_event_id"], ["user_state_events.id"]),
    )


def downgrade() -> None:
    op.drop_table("step_completion_inheritances")

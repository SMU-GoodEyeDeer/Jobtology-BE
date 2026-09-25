from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260922_03"
down_revision: str | Sequence[str] | None = "20260922_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recompute_contexts",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["request_id"], ["recompute_requests.id"]),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.add_column(
        "outbox_jobs",
        sa.Column("recompute_request_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE outbox_jobs AS job "
            "SET recompute_request_id = request.id "
            "FROM recompute_requests AS request "
            "WHERE job.kind = 'RECOMPUTE' "
            "AND job.payload ->> 'recompute_request_id' = request.id::text"
        )
    )
    op.create_foreign_key(
        "outbox_jobs_recompute_request_id_fkey",
        "outbox_jobs",
        "recompute_requests",
        ["recompute_request_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "outbox_jobs_recompute_request_id_key",
        "outbox_jobs",
        ["recompute_request_id"],
    )
    op.create_index(
        "outbox_jobs_recompute_request_id_index",
        "outbox_jobs",
        ["recompute_request_id"],
    )
    op.create_unique_constraint(
        "user_capabilities_completion_entity_key",
        "user_capabilities",
        ["source_completion_event_id", "entity_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "user_capabilities_completion_entity_key",
        "user_capabilities",
        type_="unique",
    )
    op.drop_index("outbox_jobs_recompute_request_id_index", table_name="outbox_jobs")
    op.drop_constraint(
        "outbox_jobs_recompute_request_id_key",
        "outbox_jobs",
        type_="unique",
    )
    op.drop_constraint(
        "outbox_jobs_recompute_request_id_fkey",
        "outbox_jobs",
        type_="foreignkey",
    )
    op.drop_column("outbox_jobs", "recompute_request_id")
    op.drop_table("recompute_contexts")

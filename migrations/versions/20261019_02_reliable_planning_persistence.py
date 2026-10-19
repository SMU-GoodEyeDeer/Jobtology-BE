"""Preserve solver schedules and make automatic roadmaps a durable follow-up."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261019_02"
down_revision: str | Sequence[str] | None = "20261019_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("recompute_requests", sa.Column(
        "auto_roadmap_pending", sa.Boolean(), nullable=False, server_default=sa.text("false"),
    ))
    op.create_index(
        "recompute_requests_auto_roadmap_pending_index", "recompute_requests",
        ["created_at", "id"], postgresql_where=sa.text("auto_roadmap_pending = true"),
    )
    # Reconcile historical successful requests too, including old orphaned drafts.
    # The runtime settles stale/infeasible requests without changing their roadmaps.
    op.execute(sa.text("""
        UPDATE recompute_requests SET auto_roadmap_pending = true
        WHERE state = 'READY' AND proposal_id IS NOT NULL
    """))
    # Only repair uniquely matched steps from the roadmap's own originating
    # proposal. Guard casts so malformed legacy JSON cannot abort a deployment.
    # Existing timestamps (including user changes) are never overwritten.
    op.execute(sa.text(r"""
        WITH proposal_steps AS (
            SELECT rm.id AS roadmap_id, step->>'step_key' AS step_key,
                CASE WHEN
                    step->>'planned_start_at' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
                    AND pg_input_is_valid(step->>'planned_start_at', 'timestamp with time zone')
                THEN (step->>'planned_start_at')::timestamptz END AS planned_start,
                CASE WHEN
                    step->>'planned_end_at' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
                    AND pg_input_is_valid(step->>'planned_end_at', 'timestamp with time zone')
                THEN (step->>'planned_end_at')::timestamptz END AS planned_end,
                count(*) OVER (PARTITION BY rm.id, step->>'step_key') AS matches
            FROM roadmaps rm
            JOIN route_proposals rp ON rp.id = rm.proposal_id AND rp.user_id = rm.user_id
            JOIN analyses a ON a.id = rp.analysis_id
                AND a.user_id = rm.user_id AND a.goal_id = rm.goal_id
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(rp.steps) = 'array' THEN rp.steps ELSE '[]'::jsonb END
            ) AS step
        )
        UPDATE roadmap_steps rs
        SET planned_start = COALESCE(rs.planned_start, ps.planned_start),
            planned_end = COALESCE(rs.planned_end, ps.planned_end)
        FROM proposal_steps ps
        WHERE rs.roadmap_id = ps.roadmap_id AND rs.step_key = ps.step_key
            AND ps.matches = 1
            AND ps.planned_start IS NOT NULL AND ps.planned_end IS NOT NULL
            AND ps.planned_end >= ps.planned_start
            AND COALESCE(rs.planned_end, ps.planned_end)
                >= COALESCE(rs.planned_start, ps.planned_start)
            AND (rs.planned_start IS NULL OR rs.planned_end IS NULL)
    """))


def downgrade() -> None:
    op.drop_index("recompute_requests_auto_roadmap_pending_index", table_name="recompute_requests")
    op.drop_column("recompute_requests", "auto_roadmap_pending")

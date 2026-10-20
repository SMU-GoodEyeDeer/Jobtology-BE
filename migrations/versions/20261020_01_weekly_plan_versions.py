"""Version weekly overrides and retain reset revisions/completed schedules."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '20261020_01'
down_revision: str | Sequence[str] | None = '20261019_03'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('recompute_requests', sa.Column('failure_details', postgresql.JSONB(), nullable=True))
    op.add_column('roadmap_plan_overrides', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('roadmap_plan_overrides', sa.Column('is_customized', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('roadmap_plan_overrides', sa.Column('step_schedules', postgresql.JSONB(), nullable=False,
                                                   server_default=sa.text("'{}'::jsonb")))
    op.create_check_constraint('roadmap_plan_overrides_version_check', 'roadmap_plan_overrides', 'version >= 1')


def downgrade() -> None:
    op.drop_column('recompute_requests', 'failure_details')
    op.drop_constraint('roadmap_plan_overrides_version_check', 'roadmap_plan_overrides')
    op.drop_column('roadmap_plan_overrides', 'step_schedules')
    op.drop_column('roadmap_plan_overrides', 'is_customized')
    op.drop_column('roadmap_plan_overrides', 'version')

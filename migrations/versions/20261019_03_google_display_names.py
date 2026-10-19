"""Store the optional display name from verified Google profile claims."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261019_03"
down_revision: str | Sequence[str] | None = "20261019_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "display_name")

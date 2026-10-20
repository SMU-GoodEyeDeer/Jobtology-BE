"""Persist optional local weekly availability windows and excluded dates."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261020_02"
down_revision: str | Sequence[str] | None = "20261020_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ("weekly_availability", "unavailable_dates"):
        op.add_column("route_preferences", sa.Column(
            name, postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb"),
        ))
        op.create_check_constraint(
            f"route_preferences_{name}_array_check", "route_preferences",
            f"jsonb_typeof({name}) = 'array' AND jsonb_array_length({name}) <= "
            + ("168" if name == "weekly_availability" else "366"),
        )


def downgrade() -> None:
    for name in ("unavailable_dates", "weekly_availability"):
        op.drop_constraint(f"route_preferences_{name}_array_check", "route_preferences")
        op.drop_column("route_preferences", name)

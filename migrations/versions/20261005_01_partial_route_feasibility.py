from collections.abc import Sequence

from alembic import op

revision: str = "20261005_01"
down_revision: str | Sequence[str] | None = "20260922_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("route_proposals_feasibility_check", "route_proposals", type_="check")
    op.create_check_constraint(
        "route_proposals_feasibility_check",
        "route_proposals",
        "feasibility IN ('FEASIBLE', 'RISKY', 'PARTIAL', 'INFEASIBLE')",
    )


def downgrade() -> None:
    op.execute("UPDATE route_proposals SET feasibility = 'INFEASIBLE' WHERE feasibility = 'PARTIAL'")
    op.drop_constraint("route_proposals_feasibility_check", "route_proposals", type_="check")
    op.create_check_constraint(
        "route_proposals_feasibility_check",
        "route_proposals",
        "feasibility IN ('FEASIBLE', 'RISKY', 'INFEASIBLE')",
    )

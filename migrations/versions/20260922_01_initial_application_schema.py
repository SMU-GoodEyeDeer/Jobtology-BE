from collections.abc import Sequence

from alembic import op

from jobtology_be.infrastructure.persistence.initial_schema_20260922_01 import INITIAL_METADATA

revision: str = "20260922_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    INITIAL_METADATA.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade() -> None:
    INITIAL_METADATA.drop_all(bind=op.get_bind(), checkfirst=False)

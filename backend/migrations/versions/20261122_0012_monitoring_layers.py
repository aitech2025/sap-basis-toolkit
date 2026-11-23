"""Per-system monitoring layers (SAP application, database, OS). Existing systems keep full monitoring (NULL)."""
from alembic import op

from app.db import add_missing_columns

revision = "20261122_0012"
down_revision = "20261118_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_missing_columns(op.get_bind())


def downgrade() -> None:
    pass

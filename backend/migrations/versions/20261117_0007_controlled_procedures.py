"""Controlled start/stop procedures: step log, checklist, force and abort flags on system_operations."""
from alembic import op

from app.db import add_missing_columns

revision = "20261117_0007"
down_revision = "20261117_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_missing_columns(op.get_bind())


def downgrade() -> None:
    pass

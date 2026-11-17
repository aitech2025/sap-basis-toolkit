"""Configuration snapshots and security baseline drift detection."""
from sqlalchemy import inspect
from alembic import op

from app.db import configuration_snapshots, create_missing_tables

revision = "20261117_0007"
down_revision = "20261117_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    create_missing_tables(bind, [configuration_snapshots] if configuration_snapshots.name not in existing else [])


def downgrade() -> None:
    pass

"""Add toolkit modules: generic alerts, imported STRUST certificates, table growth snapshots."""
from sqlalchemy import inspect
from alembic import op

from app.db import (
    alert_events, capacity_snapshots, create_missing_tables, imported_certificates,
    table_size_snapshots,
)

revision = "20261117_0002"
down_revision = "20261117_0001"
branch_labels = None
depends_on = None

NEW_TABLES = (alert_events, imported_certificates, table_size_snapshots, capacity_snapshots)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    create_missing_tables(bind, [t for t in NEW_TABLES if t.name not in existing])


def downgrade() -> None:
    # Deliberately retain monitoring history.
    pass

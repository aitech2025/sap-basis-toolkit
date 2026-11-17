"""Multi-system platform: SAP system registry, alerts, STRUST certificates, growth, analysis history."""
from sqlalchemy import inspect
from alembic import op

from app.db import (
    add_missing_columns, alert_events, analysis_runs, capacity_snapshots, create_missing_tables,
    imported_certificates, sap_systems, table_size_snapshots,
)

revision = "20261117_0002"
down_revision = "20261117_0001"
branch_labels = None
depends_on = None

NEW_TABLES = (sap_systems, alert_events, imported_certificates, table_size_snapshots,
              capacity_snapshots, analysis_runs)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    create_missing_tables(bind, [t for t in NEW_TABLES if t.name not in existing])
    add_missing_columns(bind)


def downgrade() -> None:
    # Deliberately retain monitoring history.
    pass

"""Monitoring platform: users, audit log, collectors, monitor snapshots, metrics, rules, incidents, maintenance."""
from sqlalchemy import inspect
from alembic import op

from app.db import (
    alert_rules, audit_log, collectors, create_missing_tables, incident_events, incidents, maintenance_windows,
    metric_samples, monitor_snapshots, users,
)

revision = "20261117_0005"
down_revision = "20261117_0004"
branch_labels = None
depends_on = None

TABLES = (users, audit_log, collectors, monitor_snapshots, metric_samples, alert_rules, incidents,
          incident_events, maintenance_windows)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    create_missing_tables(bind, [t for t in TABLES if t.name not in existing])


def downgrade() -> None:
    pass

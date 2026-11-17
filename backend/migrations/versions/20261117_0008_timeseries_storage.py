"""Move numeric telemetry to a TimescaleDB-ready time-series table.

Existing metric_samples rows are copied forward. metric_samples is retained for
rollback/compatibility during the transition; new writes use metric_points.
"""
import logging

from alembic import op
from sqlalchemy import inspect, text

from app.db import create_missing_tables, metric_points

revision = "20261117_0008"
down_revision = "20261117_0007"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    if metric_points.name not in existing:
        create_missing_tables(bind, [metric_points])

    # Preserve the existing metric history before switching all new writes.
    if "metric_samples" in existing:
        bind.execute(text(
            "INSERT INTO metric_points (system_id, metric, value, ts) "
            "SELECT system_id, metric, value, ts FROM metric_samples"
        ))

    if bind.dialect.name != "postgresql":
        return

    # TimescaleDB is optional for externally managed PostgreSQL installations.
    # A regular PostgreSQL table remains fully functional when the extension is
    # unavailable; Docker Compose ships the TimescaleDB-enabled PostgreSQL image.
    try:
        with bind.begin_nested():
            bind.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
    except Exception as exc:  # pragma: no cover - depends on external DB packaging
        logger.warning("TimescaleDB extension unavailable; using PostgreSQL table storage: %s", exc)
        return

    enabled = bind.execute(text(
        "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb')"
    )).scalar()
    if enabled:
        bind.execute(text(
            "SELECT create_hypertable("
            "'metric_points', 'ts', "
            "chunk_time_interval => INTERVAL '1 day', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        ))


def downgrade() -> None:
    # Keep metric_points and its data on downgrade; dropping telemetry history
    # during an application rollback would be destructive.
    pass

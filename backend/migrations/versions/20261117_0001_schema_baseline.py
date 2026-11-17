"""Idempotent schema baseline and target uniqueness enforcement."""
from sqlalchemy import inspect
from sqlalchemy.schema import CreateTable
from alembic import op

from app.db import metadata, assert_no_duplicate_targets, ensure_target_uniqueness

revision = "20261117_0001"
down_revision = None
branch_labels = None
depends_on = None

BASELINE_TABLES = {"targets", "certificate_checks", "notification_events"}


def upgrade() -> None:
    bind = op.get_bind()
    assert_no_duplicate_targets(bind)
    existing_tables = set(inspect(bind).get_table_names())
    # Restrict to the baseline tables; later revisions own the tables they add.
    missing_tables = [
        table for table in metadata.sorted_tables
        if table.name in BASELINE_TABLES and table.name not in existing_tables
    ]
    for table in missing_tables:
        bind.execute(CreateTable(table))
    ensure_target_uniqueness(bind)


def downgrade() -> None:
    # Deliberately retain application tables/data.
    pass

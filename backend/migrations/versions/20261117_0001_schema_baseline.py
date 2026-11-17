"""Idempotent schema baseline and target uniqueness enforcement.

The baseline schema is frozen here (not imported from app.db) so later model
changes cannot alter what this revision creates.
"""
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text, inspect, text,
)
from sqlalchemy.schema import CreateTable
from alembic import op

from app.db import assert_no_duplicate_targets, ensure_target_uniqueness

revision = "20261117_0001"
down_revision = None
branch_labels = None
depends_on = None

_md = MetaData()
_targets = Table(
    "targets", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False),
    Column("hostname", String(253), nullable=False),
    Column("port", Integer, nullable=False, default=443),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("source", String(30), nullable=False, server_default="endpoint"),
    Column("sap_system_id", String(3)),
    Column("sap_client", String(3)),
    Column("pse_name", String(255)),
)
_checks = Table(
    "certificate_checks", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("target_id", Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False),
    Column("checked_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("subject", Text),
    Column("issuer", Text),
    Column("serial_number", String(128)),
    Column("not_before", String(40)),
    Column("not_after", String(40)),
    Column("days_remaining", Integer),
    Column("tls_verified", Boolean, nullable=False, default=False),
    Column("status", String(30), nullable=False),
    Column("error", Text),
)
_notifications = Table(
    "notification_events", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("target_id", Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False),
    Column("fingerprint", String(300), nullable=False),
    Column("channel", String(30), nullable=False),
    Column("sent_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("result", String(30), nullable=False),
    Column("error", Text),
)


def upgrade() -> None:
    bind = op.get_bind()
    assert_no_duplicate_targets(bind)
    existing_tables = set(inspect(bind).get_table_names())
    for table in _md.sorted_tables:
        if table.name not in existing_tables:
            bind.execute(CreateTable(table))
    ensure_target_uniqueness(bind)


def downgrade() -> None:
    # Deliberately retain application tables/data.
    pass

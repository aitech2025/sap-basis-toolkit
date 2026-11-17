"""Explicit SAP infrastructure dependency topology inventory.

Revision ID: 20261117_0009
Revises: 20261117_0008
"""
from alembic import op
from sqlalchemy import inspect

from app.db import create_missing_tables, topology_assets, topology_links

revision = "20261117_0009"
down_revision = "20261117_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    missing = [table for table in (topology_assets, topology_links) if table.name not in existing]
    create_missing_tables(bind, missing)


def downgrade() -> None:
    # Keep operator-maintained topology data on downgrade; dropping these tables
    # would silently erase manually curated infrastructure relationships.
    pass

"""Explicit SAP infrastructure dependency topology inventory.

Revision ID: 20261117_0009
Revises: 20261117_0008
"""
from alembic import op

from app.db import create_missing_tables, topology_assets, topology_links

revision = "20261117_0009"
down_revision = "20261117_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    create_missing_tables(bind, [topology_assets, topology_links])


def downgrade() -> None:
    # Keep operator-maintained topology data on downgrade; dropping these tables
    # would silently erase manually curated infrastructure relationships.
    pass

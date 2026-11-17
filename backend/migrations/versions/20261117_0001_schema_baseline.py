"""Idempotent schema baseline and target uniqueness enforcement.

This migration supports both fresh databases and installations created by the
original MVP's SQLAlchemy metadata.create_all() bootstrap.
"""
from alembic import op

from app.db import metadata, ensure_target_uniqueness

revision = "20261117_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind=bind)
    ensure_target_uniqueness(bind)


def downgrade() -> None:
    # Deliberately retain application tables/data. A baseline downgrade must
    # never destroy customer certificate history.
    pass

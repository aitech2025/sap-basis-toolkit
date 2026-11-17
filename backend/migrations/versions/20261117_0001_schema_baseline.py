"""Idempotent schema baseline and target uniqueness enforcement."""
from alembic import op

from app.db import metadata, assert_no_duplicate_targets, ensure_target_uniqueness

revision = "20261117_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Preflight duplicates before metadata.create_all attempts the unique index.
    assert_no_duplicate_targets(bind)
    metadata.create_all(bind=bind)
    ensure_target_uniqueness(bind)


def downgrade() -> None:
    # Deliberately retain application tables/data. A baseline downgrade must
    # never destroy customer certificate history.
    pass

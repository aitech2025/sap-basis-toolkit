"""OS-level discovery: SSH inventory collectors (pinned host key, optional private key)."""
from alembic import op

from app.db import add_missing_columns

revision = "20261118_0011"
down_revision = "20261117_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_missing_columns(op.get_bind())


def downgrade() -> None:
    pass

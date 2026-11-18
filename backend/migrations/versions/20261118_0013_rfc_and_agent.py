"""RFC collector (SAP client) and ABAP push agent tokens."""
import sqlalchemy as sa
from alembic import op

from app.db import add_missing_columns, agent_tokens

revision = "20261118_0013"
down_revision = "20261118_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    add_missing_columns(bind)
    if "agent_tokens" not in sa.inspect(bind).get_table_names():
        agent_tokens.create(bind)


def downgrade() -> None:
    op.drop_table("agent_tokens")

"""Fleet scale: pointer to the newest monitor snapshot per system and monitor."""
import sqlalchemy as sa
from alembic import op

from app.db import backfill_monitor_latest, monitor_latest

revision = "20261118_0012"
down_revision = "20261118_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "monitor_latest" not in sa.inspect(bind).get_table_names():
        monitor_latest.create(bind)
        backfill_monitor_latest(bind)


def downgrade() -> None:
    op.drop_table("monitor_latest")

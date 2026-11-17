"""Discovery inventory (instances) and lifecycle operations (start/stop/restart)."""
from sqlalchemy import inspect
from alembic import op

from app.db import add_missing_columns, create_missing_tables, sap_instances, system_operations

revision = "20261117_0006"
down_revision = "20261117_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    create_missing_tables(bind, [t for t in (sap_instances, system_operations) if t.name not in existing])
    add_missing_columns(bind)


def downgrade() -> None:
    pass

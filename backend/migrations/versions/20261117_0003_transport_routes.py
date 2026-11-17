"""Transport routes between SAP systems (landscape topology)."""
from sqlalchemy import inspect
from alembic import op

from app.db import create_missing_tables, transport_routes

revision = "20261117_0003"
down_revision = "20261117_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "transport_routes" not in set(inspect(bind).get_table_names()):
        create_missing_tables(bind, [transport_routes])


def downgrade() -> None:
    pass

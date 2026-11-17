"""Operations monitoring: transport import history, background jobs, SAP locks, system timezone."""
from sqlalchemy import inspect
from alembic import op

from app.db import (
    add_missing_columns, create_missing_tables, job_runs, lock_entries, lock_snapshots, transport_imports,
)

revision = "20261117_0004"
down_revision = "20261117_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    create_missing_tables(bind, [t for t in (transport_imports, job_runs, lock_snapshots, lock_entries)
                                 if t.name not in existing])
    add_missing_columns(bind)


def downgrade() -> None:
    pass

"""Basis daily checks: latest value per metric and daily check reviews."""
import sqlalchemy as sa
from alembic import op

from app.db import backfill_metric_latest, daily_check_reviews, metric_latest

revision = "20261118_0014"
down_revision = "20261118_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if "metric_latest" not in tables:
        metric_latest.create(bind)
        backfill_metric_latest(bind)
    if "daily_check_reviews" not in tables:
        daily_check_reviews.create(bind)


def downgrade() -> None:
    op.drop_table("daily_check_reviews")
    op.drop_table("metric_latest")

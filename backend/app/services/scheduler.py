import logging
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from ..config import CHECK_INTERVAL_MINUTES
from ..db import get_db, targets
from .checks import check_target
logger = logging.getLogger(__name__)
_scheduler = None

def run_scheduled_checks() -> None:
    with get_db() as connection:
        ids = connection.execute(select(targets.c.id).where(targets.c.enabled.is_(True))).scalars().all()
    for target_id in ids:
        try: check_target(target_id)
        except Exception: logger.exception("Scheduled certificate check failed for target %s", target_id)

def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running: return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(run_scheduled_checks, "interval", minutes=max(1, CHECK_INTERVAL_MINUTES), id="certificate-sweep", replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.start()

def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running: _scheduler.shutdown(wait=False)
    _scheduler = None

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

from ..config import CHECK_INTERVAL_MINUTES, GROWTH_EVAL_INTERVAL_MINUTES
from ..db import get_db, targets
from .checks import check_target

logger = logging.getLogger(__name__)


def run_scheduled_checks() -> None:
    with get_db() as connection:
        ids = connection.execute(
            select(targets.c.id).where(targets.c.enabled.is_(True))
        ).scalars().all()
    for target_id in ids:
        try:
            check_target(target_id)
        except Exception:
            logger.exception("Scheduled certificate check failed for target %s", target_id)


def run_toolkit_evaluations() -> None:
    """Daily evaluation of imported STRUST certificates and growth forecasts."""
    from .cert_import import evaluate_imported_certificates
    from .growth import evaluate_growth_alerts

    for name, job in (("imported certificates", evaluate_imported_certificates),
                      ("table growth", evaluate_growth_alerts)):
        try:
            job()
        except Exception:
            logger.exception("Scheduled %s evaluation failed", name)


def run_scheduler_forever() -> None:
    """Run exactly one scheduler process; API workers do not start schedulers."""
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_scheduled_checks,
        "interval",
        minutes=max(1, CHECK_INTERVAL_MINUTES),
        id="certificate-sweep",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc),
    )
    scheduler.add_job(
        run_toolkit_evaluations,
        "interval",
        minutes=max(1, GROWTH_EVAL_INTERVAL_MINUTES),
        id="toolkit-evaluation",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc),
    )
    logger.info("Starting certificate scheduler; interval=%s minutes", CHECK_INTERVAL_MINUTES)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Certificate scheduler stopping")
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=True)

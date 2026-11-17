import logging
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

from ..config import CHECK_INTERVAL_MINUTES, COLLECTOR_TICK_SECONDS, GROWTH_EVAL_INTERVAL_MINUTES
from ..db import get_db, sap_systems, targets
from .checks import check_target

logger = logging.getLogger(__name__)


def run_scheduled_checks() -> None:
    with get_db() as connection:
        ids = connection.execute(
            select(targets.c.id)
            .join(sap_systems, sap_systems.c.id == targets.c.system_id, isouter=True)
            .where(targets.c.enabled.is_(True))
            # Endpoints of a disabled SAP system are paused; unassigned endpoints keep running.
            .where((targets.c.system_id.is_(None)) | (sap_systems.c.enabled.is_(True)))
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


def run_collectors_tick() -> None:
    from .collectors import runner
    try:
        runner.run_due()
    except Exception:
        logger.exception("Collector tick failed")


def run_housekeeping() -> None:
    from .. import auth
    from . import monitors
    for name, job in (("metrics/snapshots", monitors.prune), ("audit log", auth.prune_audit)):
        try:
            job()
        except Exception:
            logger.exception("Housekeeping %s failed", name)


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
    scheduler.add_job(
        run_collectors_tick, "interval", seconds=max(10, COLLECTOR_TICK_SECONDS), id="collectors",
        replace_existing=True, max_instances=1, coalesce=True, next_run_time=datetime.now(timezone.utc),
    )
    scheduler.add_job(run_housekeeping, "interval", hours=6, id="housekeeping", replace_existing=True,
                      max_instances=1, coalesce=True)
    logger.info("Starting certificate scheduler; interval=%s minutes", CHECK_INTERVAL_MINUTES)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Certificate scheduler stopping")
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=True)

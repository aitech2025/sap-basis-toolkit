import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

from .. import config
from ..config import CHECK_INTERVAL_MINUTES, COLLECTOR_TICK_SECONDS, GROWTH_EVAL_INTERVAL_MINUTES
from ..db import get_db, get_engine, sap_systems, targets
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
    def one(target_id: int) -> None:
        try:
            check_target(target_id)
        except Exception:
            logger.exception("Scheduled certificate check failed for target %s", target_id)

    # TLS handshakes are network-bound: check endpoints in parallel so one unreachable host does not hold up the rest
    with ThreadPoolExecutor(max_workers=max(1, config.CERT_CHECK_PARALLEL), thread_name_prefix="cert") as pool:
        list(pool.map(one, ids))


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


_dispatcher = None


def run_collectors_tick() -> None:
    """Hand due collectors to the long-lived pool; returns immediately (runs never block the scheduler)."""
    global _dispatcher
    from .collectors import runner
    try:
        if _dispatcher is None:
            _dispatcher = runner.Dispatcher()
        _dispatcher.tick()
    except Exception:
        logger.exception("Collector tick failed")


# ---- single active scheduler ---------------------------------------------------------------------------------------
LEADER_LOCK_KEY = 0x5E471E1  # pg advisory lock id shared by all worker replicas
_leader_conn = None


def acquire_leadership(retry_seconds: int = 30) -> None:
    """Block until this process is the only active scheduler (PostgreSQL advisory lock).

    Run two or more worker replicas for availability: one schedules, the others wait and take over within
    `retry_seconds` when it stops. SQLite (development) has a single process and needs no lock."""
    global _leader_conn
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        return
    while True:
        conn = engine.raw_connection()
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (LEADER_LOCK_KEY,))
        if cur.fetchone()[0]:
            cur.close()
            _leader_conn = conn
            logger.info("This worker (pid %s) is the active scheduler", os.getpid())
            return
        cur.close()
        conn.close()
        logger.info("Another worker is the active scheduler; standing by")
        time.sleep(retry_seconds)


def check_leadership() -> None:
    """Exit if the connection holding the scheduler lock is gone, so the lock (and the work) is not held twice."""
    if _leader_conn is None:
        return
    try:
        cur = _leader_conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
    except Exception:
        logger.critical("Lost the database connection holding the scheduler lock; exiting so a standby can take over")
        os._exit(3)


def run_agent_watchdog() -> None:
    from . import agent
    try:
        agent.watchdog()
    except Exception:
        logger.exception("Agent watchdog failed")


def run_housekeeping() -> None:
    from .. import auth
    from . import monitors
    for name, job in (("metrics/snapshots", monitors.prune), ("audit log", auth.prune_audit)):
        try:
            job()
        except Exception:
            logger.exception("Housekeeping %s failed", name)


def run_scheduler_forever() -> None:
    """Run exactly one active scheduler; API workers do not start schedulers. Extra worker replicas stand by."""
    acquire_leadership()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(check_leadership, "interval", seconds=30, id="leadership", replace_existing=True,
                      max_instances=1, coalesce=True)
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
        run_collectors_tick, "interval", seconds=max(5, COLLECTOR_TICK_SECONDS), id="collectors",
        replace_existing=True, max_instances=1, coalesce=True, next_run_time=datetime.now(timezone.utc),
    )
    scheduler.add_job(run_agent_watchdog, "interval", seconds=60, id="agent-watchdog", replace_existing=True,
                      max_instances=1, coalesce=True)
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
        if _dispatcher is not None:
            _dispatcher.shutdown(wait=False)

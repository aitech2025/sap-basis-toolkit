"""Operations monitoring: background job failures (SM37/TBTCO) and long-running SAP locks (SM12).

Both are export-driven like the other modules: Basis uploads an SM37 / TBTCO export and SM12
lock-list snapshots (manually or from a scheduled script). SAP reports times in the system's
local timezone, so every timestamp is converted with the system's configured timezone.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, select, update

from .. import config
from ..db import get_db, job_runs, lock_entries, lock_snapshots
from .alerts import send_alert_once
from .tabular import (
    InputFormatError, local_time_before, parse_duration_seconds, parse_sap_datetime, parse_sap_time, pick,
    read_rows, require_columns,
)

# ------------------------------------------------------------------------------------- jobs
JOB_NAME = ("JOBNAME", "Job Name", "Job")
JOB_COUNT = ("JOBCOUNT", "Job Count", "Job ID", "Count")
JOB_STATUS = ("STATUS", "Status", "Job Status")
JOB_USER = ("SDLUNAME", "Job CreatedB", "Job Created By", "Created By", "Scheduled By", "User")
START_DATE = ("STRTDATE", "Start date", "Start Date")
START_TIME = ("STRTTIME", "Start Time")
END_DATE = ("ENDDATE", "End date", "End Date")
END_TIME = ("ENDTIME", "End Time")
DURATION = ("Duration(sec.)", "Duration (sec.)", "Duration", "DURATION")

STATUS_MAP = {
    "A": "cancelled", "CANCELED": "cancelled", "CANCELLED": "cancelled", "ABGEBROCHEN": "cancelled", "ABORTED": "cancelled",
    "F": "finished", "FINISHED": "finished", "BEENDET": "finished", "COMPLETED": "finished",
    "R": "active", "ACTIVE": "active", "AKTIV": "active", "RUNNING": "active",
    "S": "released", "RELEASED": "released", "FREIGEGEBEN": "released",
    "P": "scheduled", "SCHEDULED": "scheduled", "EINGEPLANT": "scheduled",
    "Y": "ready", "READY": "ready", "BEREIT": "ready",
    "Z": "suspended", "SUSPENDED": "suspended",
}
FINAL = {"cancelled", "finished"}


def parse_jobs(raw: bytes | str, zone, captured_at: datetime) -> list[dict]:
    rows = read_rows(raw)
    require_columns(rows, {"job name": JOB_NAME, "status": JOB_STATUS})
    return parse_job_rows(rows, zone, captured_at)


def parse_job_rows(rows: list[dict], zone, captured_at: datetime) -> list[dict]:
    """Rows keyed by normalised column names (TBTCO via SQL or a parsed export)."""
    rows = [{k: "" if v is None else str(v) for k, v in row.items()} for row in rows]
    jobs = []
    for row in rows:
        name = pick(row, *JOB_NAME).strip()
        status = STATUS_MAP.get(pick(row, *JOB_STATUS).strip().upper())
        if not name or not status:
            continue
        start = parse_sap_datetime(pick(row, *START_DATE), pick(row, *START_TIME), zone)
        end = parse_sap_datetime(pick(row, *END_DATE), pick(row, *END_TIME), zone)
        duration = parse_duration_seconds(pick(row, *DURATION))
        if duration is None and start and end:
            duration = int((end - start).total_seconds())
        if end is None and start and duration is not None and status in FINAL:
            end = start + timedelta(seconds=duration)
        jobs.append({
            "job_name": name[:64], "job_count": pick(row, *JOB_COUNT).strip() or None, "status": status,
            "scheduled_by": pick(row, *JOB_USER).strip() or None, "start_at": start, "end_at": end,
            "duration_s": duration, "captured_at": captured_at,
        })
    if not jobs:
        raise InputFormatError("No job rows recognised: check the job name and status columns")
    return jobs


def store_jobs(system: dict, raw: bytes, zone, captured_at: datetime | None = None) -> dict:
    """Upsert job runs from an SM37/TBTCO export."""
    captured_at = captured_at or datetime.now(timezone.utc)
    return store_job_list(system, parse_jobs(raw, zone, captured_at), captured_at)


def store_job_list(system: dict, jobs: list[dict], captured_at: datetime) -> dict:
    """Upsert job runs; returns counts and newly seen cancellations (which trigger alerts)."""
    inserted = updated = 0
    new_failures = []
    with get_db() as connection:
        for job in jobs:
            key = [job_runs.c.system_id == system["id"], job_runs.c.job_name == job["job_name"]]
            if job["job_count"]:
                key.append(job_runs.c.job_count == job["job_count"])
            elif job["start_at"]:
                key.append(job_runs.c.start_at == job["start_at"])
            else:
                key.append(job_runs.c.id == -1)  # cannot identify the run: always insert
            existing = connection.execute(select(job_runs.c.id, job_runs.c.status).where(and_(*key))).first()
            if existing:
                if existing.status != job["status"] or job["status"] not in FINAL:
                    connection.execute(update(job_runs).where(job_runs.c.id == existing.id).values(**job))
                    updated += 1
                    if job["status"] == "cancelled" and existing.status != "cancelled":
                        new_failures.append(job)
                continue
            connection.execute(job_runs.insert().values(system_id=system["id"], **job))
            inserted += 1
            if job["status"] == "cancelled":
                new_failures.append(job)
        connection.execute(delete(job_runs).where(
            job_runs.c.system_id == system["id"],
            job_runs.c.captured_at < captured_at - timedelta(days=config.OPS_RETENTION_DAYS)))
    _alert_job_failures(system, new_failures)
    return {"rows": len(jobs), "inserted": inserted, "updated": updated, "new_failures": len(new_failures)}


def _alert_job_failures(system: dict, failures: list[dict]) -> None:
    # One alert per job name per day, however many runs were cancelled.
    by_name: dict[str, list[dict]] = defaultdict(list)
    for job in failures:
        by_name[job["job_name"]].append(job)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for name, runs in by_name.items():
        last = max(runs, key=lambda j: j["start_at"] or j["captured_at"])
        send_alert_once(
            "background-jobs", f"job:{system['id']}:{name}:{day}",
            f"{system['sid']} ({system['landscape']}): background job {name} cancelled"
            + (f" ({len(runs)} runs)" if len(runs) > 1 else ""),
            "error",
            {"sid": system["sid"], "job": name, "job_count": last["job_count"], "scheduled_by": last["scheduled_by"],
             "started": last["start_at"].isoformat() if last["start_at"] else None,
             "next_step": "Check the job log in SM37 and the short dump in ST22"},
            system_id=system["id"], incident_key=f"job:{system['id']}:{name}",
        )


def _aware(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value):
    value = _aware(value)
    return value.isoformat() if value else None


def job_report(system_id: int, days: int = 7) -> dict:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    with get_db() as connection:
        rows = [dict(r) for r in connection.execute(
            select(job_runs).where(job_runs.c.system_id == system_id)
            .order_by(job_runs.c.start_at.desc().nullslast(), job_runs.c.id.desc())
        ).mappings().all()]
    for r in rows:
        for k in ("start_at", "end_at", "captured_at"):
            r[k] = _aware(r[k])
    window = [r for r in rows if (r["start_at"] or r["captured_at"]) >= since]
    last_capture = max((r["captured_at"] for r in rows), default=None)

    groups: dict[str, dict] = {}
    for r in window:
        g = groups.setdefault(r["job_name"], {"job_name": r["job_name"], "runs": 0, "cancelled": 0, "finished": 0,
                                              "last_failure": None, "last_status": None, "last_run": None,
                                              "scheduled_by": r["scheduled_by"]})
        g["runs"] += 1
        if r["status"] in ("cancelled", "finished"):
            g[r["status"]] += 1
        started = r["start_at"] or r["captured_at"]
        if g["last_run"] is None or started > g["last_run"]:
            g["last_run"], g["last_status"] = started, r["status"]
        if r["status"] == "cancelled" and (g["last_failure"] is None or started > g["last_failure"]):
            g["last_failure"] = started
    failing = []
    for g in groups.values():
        if not g["cancelled"]:
            continue
        completed = g["cancelled"] + g["finished"]
        g["failure_rate_pct"] = round(g["cancelled"] / completed * 100, 1) if completed else None
        g["still_failing"] = g["last_status"] == "cancelled"
        failing.append(g)
    failing.sort(key=lambda g: (not g["still_failing"], -g["cancelled"], g["job_name"]))
    for g in failing:
        g["last_failure"], g["last_run"] = _iso(g["last_failure"]), _iso(g["last_run"])

    reference = last_capture or now
    long_running = []
    for r in rows:
        if r["status"] == "active" and r["start_at"]:
            minutes = int((reference - r["start_at"]).total_seconds() // 60)
            if minutes >= config.JOB_LONG_RUNNING_MINUTES:
                long_running.append({"job_name": r["job_name"], "job_count": r["job_count"], "scheduled_by": r["scheduled_by"],
                                     "start_at": _iso(r["start_at"]), "running_minutes": minutes})
    long_running.sort(key=lambda j: -j["running_minutes"])

    recent_failures = [{
        "job_name": r["job_name"], "job_count": r["job_count"], "scheduled_by": r["scheduled_by"],
        "start_at": _iso(r["start_at"]), "duration_s": r["duration_s"],
    } for r in window if r["status"] == "cancelled"][:50]
    counts = Counter(r["status"] for r in window)
    last_24h = [r for r in window if (r["start_at"] or r["captured_at"]) >= now - timedelta(hours=24)]
    completed = counts["cancelled"] + counts["finished"]
    return {
        "days": days,
        "last_capture": _iso(last_capture),
        "stale": last_capture is None or last_capture < now - timedelta(hours=config.OPS_DATA_STALE_HOURS),
        "summary": {
            "runs": len(window), "cancelled": counts["cancelled"], "finished": counts["finished"],
            "active": counts["active"], "scheduled": counts["released"] + counts["scheduled"] + counts["ready"],
            "failure_rate_pct": round(counts["cancelled"] / completed * 100, 1) if completed else None,
            "cancelled_24h": sum(1 for r in last_24h if r["status"] == "cancelled"),
            "failing_jobs": len(failing), "long_running": len(long_running),
            "long_running_threshold_minutes": config.JOB_LONG_RUNNING_MINUTES,
        },
        "failing_jobs": failing,
        "recent_failures": recent_failures,
        "long_running": long_running,
    }


# ------------------------------------------------------------------------------------ locks
LOCK_CLIENT = ("Cli", "Client", "GCLIENT", "MANDT")
LOCK_USER = ("User", "GUNAME", "User Name", "Owner")
LOCK_TIME = ("Time", "GTTIME", "Lock Time")
LOCK_DATE = ("Date", "GTDATE", "Lock Date")
LOCK_MODE = ("Mode", "Shared", "GMODE", "Lock Mode", "Lock mode")
LOCK_TABLE = ("Table", "GNAME", "Table Name")
LOCK_ARG = ("Lock Argument", "Lock argument", "GARG", "Argument")


def parse_locks(raw: bytes | str, zone, captured_at: datetime) -> list[dict]:
    rows = read_rows(raw)
    require_columns(rows, {"user": LOCK_USER, "table": LOCK_TABLE, "time": LOCK_TIME})
    locks = []
    for row in rows:
        user, table = pick(row, *LOCK_USER).strip(), pick(row, *LOCK_TABLE).strip().upper()
        t = parse_sap_time(pick(row, *LOCK_TIME))
        if not user or not table or t is None:
            continue
        date_value = pick(row, *LOCK_DATE)
        locked_at = parse_sap_datetime(date_value, pick(row, *LOCK_TIME), zone) if date_value else None
        if locked_at is None:
            locked_at = local_time_before(captured_at, t, zone)  # SM12 without date: most recent occurrence
        age = max(0, int((captured_at - locked_at).total_seconds() // 60))
        locks.append({
            "client": pick(row, *LOCK_CLIENT).strip()[:3] or None, "user_name": user[:40],
            "lock_mode": pick(row, *LOCK_MODE).strip()[:2] or None, "table_name": table[:64],
            "argument": pick(row, *LOCK_ARG).strip() or None, "locked_at": locked_at, "age_minutes": age,
        })
    return locks


def store_locks(system: dict, raw: bytes, zone, captured_at: datetime | None = None) -> dict:
    """SM12 export upload."""
    captured_at = captured_at or datetime.now(timezone.utc)
    rows = read_rows(raw) if raw.strip() else []
    locks = parse_locks(raw, zone, captured_at) if rows else []  # an empty SM12 list is a valid snapshot
    return store_lock_list(system, locks, captured_at, total=len(locks))


def last_lock_snapshot_at(system_id: int) -> datetime | None:
    with get_db() as connection:
        at = connection.execute(select(func.max(lock_snapshots.c.captured_at))
                                .where(lock_snapshots.c.system_id == system_id)).scalar()
    return _aware(at) if at else None


def store_lock_list(system: dict, locks: list[dict], captured_at: datetime, total: int | None = None) -> dict:
    """Persist one lock snapshot. Locks without a known time (owner ID without timestamp) inherit the time they were first
    seen in an earlier snapshot, so their age is a lower bound rather than invented."""
    with get_db() as connection:
        unknown = [lock for lock in locks if lock.get("locked_at") is None]
        if unknown:
            prev = connection.execute(select(lock_snapshots.c.id).where(lock_snapshots.c.system_id == system["id"])
                                      .order_by(lock_snapshots.c.captured_at.desc()).limit(1)).scalar()
            known = {}
            if prev:
                for e in connection.execute(select(lock_entries).where(lock_entries.c.snapshot_id == prev)).mappings():
                    known[(e["user_name"], e["table_name"], e["argument"])] = _aware(e["locked_at"])
            for lock in unknown:
                lock["locked_at"] = known.get((lock["user_name"], lock["table_name"], lock["argument"]), captured_at)
                lock["age_minutes"] = max(0, int((captured_at - lock["locked_at"]).total_seconds() // 60))
        rows = [{k: lock.get(k) for k in ("client", "user_name", "lock_mode", "table_name", "argument", "locked_at", "age_minutes")}
                for lock in locks]
        snapshot_id = connection.execute(lock_snapshots.insert().values(
            system_id=system["id"], captured_at=captured_at, total=len(locks) if total is None else total)
            .returning(lock_snapshots.c.id)).scalar_one()
        if rows:
            connection.execute(lock_entries.insert(), [{**r, "snapshot_id": snapshot_id} for r in rows])
        connection.execute(delete(lock_snapshots).where(
            lock_snapshots.c.system_id == system["id"],
            lock_snapshots.c.captured_at < captured_at - timedelta(days=config.OPS_RETENTION_DAYS)))
    long_locks = [lock for lock in locks if lock["age_minutes"] >= config.LOCK_WARNING_MINUTES]
    for lock in long_locks:
        severity = "critical" if lock["age_minutes"] >= config.LOCK_CRITICAL_MINUTES else "warning"
        send_alert_once(
            "sap-locks",
            f"lock:{system['id']}:{lock['user_name']}:{lock['table_name']}:{lock['argument']}:{lock['locked_at'].isoformat()}:{severity}",
            f"{system['sid']} ({system['landscape']}): lock on {lock['table_name']} held by {lock['user_name']} "
            f"for {lock['age_minutes']} min",
            severity,
            {"sid": system["sid"], "user": lock["user_name"], "table": lock["table_name"], "argument": lock["argument"],
             "client": lock["client"], "locked_since": lock["locked_at"].isoformat(),
             "next_step": "Check the owner's session in SM04/SM50 before deleting the lock in SM12"},
            system_id=system["id"],
            incident_key=f"lock:{system['id']}:{lock['user_name']}:{lock['table_name']}:{lock['argument']}:{lock['locked_at'].isoformat()}",
        )
    return {"snapshot_id": snapshot_id, "captured_at": captured_at.isoformat(), "locks": total if total is not None else len(locks),
            "long_running": len(long_locks)}


def lock_report(system_id: int, history_days: int = 7) -> dict:
    now = datetime.now(timezone.utc)
    with get_db() as connection:
        snapshots = [dict(r) for r in connection.execute(
            select(lock_snapshots).where(lock_snapshots.c.system_id == system_id,
                                         lock_snapshots.c.captured_at >= now - timedelta(days=history_days))
            .order_by(lock_snapshots.c.captured_at)).mappings().all()]
        if not snapshots:
            latest = connection.execute(select(lock_snapshots).where(lock_snapshots.c.system_id == system_id)
                                        .order_by(lock_snapshots.c.captured_at.desc()).limit(1)).mappings().first()
            snapshots = [dict(latest)] if latest else []
        ids = [s["id"] for s in snapshots]
        entries = [dict(r) for r in connection.execute(
            select(lock_entries).where(lock_entries.c.snapshot_id.in_(ids))).mappings().all()] if ids else []
    if not snapshots:
        return {"latest": None, "stale": True, "summary": None, "long_running": [], "by_user": [], "by_table": [],
                "persistent": [], "history": [], "thresholds": _thresholds()}

    by_snapshot = defaultdict(list)
    for e in entries:
        e["locked_at"] = _aware(e["locked_at"])
        by_snapshot[e["snapshot_id"]].append(e)
    latest = snapshots[-1]
    latest_at = _aware(latest["captured_at"])
    current = by_snapshot[latest["id"]]

    def identity(e):
        return (e["user_name"], e["table_name"], e["argument"], e["locked_at"])
    seen = Counter(identity(e) for s in snapshots for e in by_snapshot[s["id"]])

    def view(e):
        return {"user": e["user_name"], "client": e["client"], "table": e["table_name"], "mode": e["lock_mode"],
                "argument": e["argument"], "locked_at": _iso(e["locked_at"]), "age_minutes": e["age_minutes"],
                "severity": "critical" if e["age_minutes"] >= config.LOCK_CRITICAL_MINUTES
                else "warning" if e["age_minutes"] >= config.LOCK_WARNING_MINUTES else "ok",
                "seen_in_snapshots": seen[identity(e)]}

    long_running = sorted((view(e) for e in current if e["age_minutes"] >= config.LOCK_WARNING_MINUTES),
                          key=lambda x: -x["age_minutes"])
    by_user = Counter(e["user_name"] for e in current)
    by_table = Counter(e["table_name"] for e in current)
    persistent = sorted((view(e) for e in current if seen[identity(e)] > 1), key=lambda x: -x["age_minutes"])
    history = [{"captured_at": _iso(s["captured_at"]), "total": s["total"],
                "long_running": sum(1 for e in by_snapshot[s["id"]] if e["age_minutes"] >= config.LOCK_WARNING_MINUTES)}
               for s in snapshots]
    return {
        "latest": _iso(latest_at),
        "stale": latest_at < now - timedelta(hours=config.OPS_DATA_STALE_HOURS),
        "summary": {
            # total from the snapshot: live snapshots store only locks held LOCK_STORE_MIN_AGE_MINUTES or longer
            "locks": latest["total"], "locks_listed": len(current), "long_running": len(long_running),
            "critical": sum(1 for x in long_running if x["severity"] == "critical"),
            "oldest_minutes": max((e["age_minutes"] for e in current), default=None),
            "users": len(by_user), "tables": len(by_table), "snapshots": len(snapshots),
        },
        "long_running": long_running[:100],
        "by_user": [{"user": u, "locks": n} for u, n in by_user.most_common(10)],
        "by_table": [{"table": t, "locks": n} for t, n in by_table.most_common(10)],
        "persistent": persistent[:50],
        "history": history,
        "thresholds": _thresholds(),
    }


def _thresholds() -> dict:
    return {"warning_minutes": config.LOCK_WARNING_MINUTES, "critical_minutes": config.LOCK_CRITICAL_MINUTES}

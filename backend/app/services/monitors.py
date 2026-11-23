"""Normalised live monitors, metric samples and export-upload fallbacks.

Every monitor is stored as a snapshot (JSON) with a health, and contributes metric samples that
the incident rules evaluate. Sources: sapcontrol, HANA SQL, uploads (ST22, SM21, SM13, SM58,
SMQ1/SMQ2, WE02) or the demo simulator - the same builders are used for all of them.
"""
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .. import config
from ..db import get_db, metric_latest, metric_points, monitor_latest, monitor_snapshots
from .tabular import InputFormatError, parse_number, parse_sap_datetime, pick, read_rows, require_columns

MONITORS = ("availability", "workprocesses", "dumps", "syslog", "updates", "trfc", "qrfc", "idocs", "hana", "host", "abap_live",
            "number_ranges", "applog", "batch_input", "security", "spool")
STATUS_COLOR = {"GREEN": "green", "YELLOW": "yellow", "RED": "red", "GRAY": "gray", "GREY": "gray"}
WP_BUSY = {"run", "running", "hold", "on hold"}
WP_BROKEN = {"stop", "stopped", "ended", "end", "complete"}

IDOC_ERROR_STATUSES = {
    "02": "Error passing data to port", "04": "Error within control information of EDI subsystem",
    "05": "Error during translation", "25": "Processing despite syntax error (outbound)",
    "26": "Error during syntax check of IDoc (outbound)", "29": "Error in ALE service (outbound)",
    "51": "Application document not posted", "56": "IDoc with errors added", "60": "Error during syntax check (inbound)",
    "61": "Processing despite syntax error (inbound)", "63": "Error passing IDoc to application",
    "65": "Error in ALE service (inbound)",
}
QRFC_BLOCKED = {"SYSFAIL", "CPICERR", "STOP", "ANORETRY", "ARETRY"}
TRFC_ERRORS = {"SYSFAIL", "CPICERR", "ANORETRY", "SYSLOAD", "ARETRY"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


# ------------------------------------------------------------------------------- storage
def store(system_id: int, monitor: str, source: str, data: dict, health: str,
          captured_at: datetime | None = None) -> None:
    captured_at = captured_at or _now()
    with get_db() as connection:
        snapshot_id = connection.execute(monitor_snapshots.insert().values(
            system_id=system_id, monitor=monitor, source=source, captured_at=captured_at, health=health,
            data=json.dumps(data, default=str))).inserted_primary_key[0]
        pointer = (monitor_latest.c.system_id == system_id) & (monitor_latest.c.monitor == monitor)
        # an upload with an older capture time never replaces a newer snapshot
        newer = select(monitor_snapshots.c.id).where(monitor_snapshots.c.id == monitor_latest.c.snapshot_id,
                                                     monitor_snapshots.c.captured_at > captured_at).exists()
        moved = connection.execute(update(monitor_latest).where(pointer, ~newer).values(snapshot_id=snapshot_id)).rowcount
        if not moved and connection.execute(select(monitor_latest.c.snapshot_id).where(pointer)).first() is None:
            connection.execute(monitor_latest.insert().values(system_id=system_id, monitor=monitor, snapshot_id=snapshot_id))


def record_metrics(system_id: int, metrics: dict[str, float], ts: datetime | None = None) -> None:
    ts = ts or _now()
    rows = [{"system_id": system_id, "metric": k, "value": float(v), "ts": ts} for k, v in metrics.items() if v is not None]
    if rows:
        with get_db() as connection:
            connection.execute(metric_points.insert(), rows)
            _upsert_latest(connection, rows)


def _upsert_latest(connection, rows: list[dict]) -> None:
    """Keep metric_latest current (never moves backwards when an older timestamp arrives late)."""
    dialect = connection.dialect.name
    if dialect in ("postgresql", "sqlite"):
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        stmt = insert(metric_latest).values(rows)
        stmt = stmt.on_conflict_do_update(index_elements=[metric_latest.c.system_id, metric_latest.c.metric],
                                          set_={"value": stmt.excluded.value, "ts": stmt.excluded.ts},
                                          where=metric_latest.c.ts <= stmt.excluded.ts)
        connection.execute(stmt)
        return
    for r in rows:  # other databases: portable fallback
        moved = connection.execute(update(metric_latest).where(
            metric_latest.c.system_id == r["system_id"], metric_latest.c.metric == r["metric"], metric_latest.c.ts <= r["ts"])
            .values(value=r["value"], ts=r["ts"])).rowcount
        if not moved and connection.execute(select(metric_latest.c.ts).where(
                metric_latest.c.system_id == r["system_id"], metric_latest.c.metric == r["metric"])).first() is None:
            connection.execute(metric_latest.insert().values(**r))


def latest_metrics(system_ids: list[int] | None = None) -> dict[int, dict[str, tuple[float, datetime]]]:
    """{system_id: {metric: (value, ts)}} from metric_latest."""
    query = select(metric_latest)
    if system_ids is not None:
        query = query.where(metric_latest.c.system_id.in_(system_ids))
    out: dict[int, dict[str, tuple[float, datetime]]] = {}
    with get_db() as connection:
        for r in connection.execute(query).mappings():
            out.setdefault(r["system_id"], {})[r["metric"]] = (r["value"], datetime.fromisoformat(_iso(r["ts"])))
    return out


def latest(system_id: int, monitor: str | None = None) -> dict:
    """Latest snapshot per monitor: {monitor: {source, captured_at, health, stale, data}}."""
    with get_db() as connection:
        query = select(monitor_snapshots).join(monitor_latest, monitor_latest.c.snapshot_id == monitor_snapshots.c.id).where(
            monitor_latest.c.system_id == system_id)
        if monitor:
            query = query.where(monitor_latest.c.monitor == monitor)
        rows = connection.execute(query).mappings().all()
    stale_before = _now() - timedelta(hours=config.OPS_DATA_STALE_HOURS)
    result = {}
    for r in rows:
        captured = datetime.fromisoformat(_iso(r["captured_at"]))
        result[r["monitor"]] = {"source": r["source"], "captured_at": captured.isoformat(), "health": r["health"],
                                "stale": captured < stale_before, "data": json.loads(r["data"])}
    return result


SERIES_MAX_POINTS = 360


def series(system_id: int, metrics: list[str], hours: int = 24) -> dict[str, list[dict]]:
    since = _now() - timedelta(hours=hours)
    with get_db() as connection:
        rows = connection.execute(select(metric_points.c.metric, metric_points.c.value, metric_points.c.ts).where(
            metric_points.c.system_id == system_id, metric_points.c.metric.in_(metrics), metric_points.c.ts >= since,
        ).order_by(metric_points.c.ts)).all()
    out: dict[str, list[dict]] = {m: [] for m in metrics}
    if hours <= 24:
        for metric, value, ts in rows:
            out[metric].append({"ts": _iso(ts), "value": value})
        return out
    # Longer windows: average into at most ~SERIES_MAX_POINTS buckets per metric (a week at 5-minute collection is
    # 2016 points per metric; a chart cannot show more than a few hundred anyway).
    step = max(300, int(hours * 3600 / SERIES_MAX_POINTS))
    buckets: dict[str, dict[int, list[float]]] = {m: {} for m in metrics}
    for metric, value, ts in rows:
        epoch = int(datetime.fromisoformat(_iso(ts)).timestamp())
        buckets[metric].setdefault(epoch - epoch % step, []).append(float(value))
    for metric, b in buckets.items():
        out[metric] = [{"ts": datetime.fromtimestamp(k, timezone.utc).isoformat(), "value": round(sum(v) / len(v), 4)}
                       for k, v in sorted(b.items())]
    return out


def prune() -> None:
    with get_db() as connection:
        connection.execute(delete(metric_points).where(
            metric_points.c.ts < _now() - timedelta(days=config.METRIC_RETENTION_DAYS)))
        # keep the newest snapshot per system/monitor even when old
        keep = select(monitor_latest.c.snapshot_id)
        connection.execute(delete(monitor_snapshots).where(
            monitor_snapshots.c.captured_at < _now() - timedelta(days=config.SNAPSHOT_RETENTION_DAYS),
            monitor_snapshots.c.id.notin_(keep)))
    thin_snapshots()


def thin_snapshots(now: datetime | None = None) -> int:
    """Downsample snapshot history older than SNAPSHOT_FULL_RESOLUTION_HOURS.

    Kept: the first snapshot of every hour per system and monitor, every snapshot whose health differs from the one
    before it (state changes stay exact for root-cause analysis), and the current snapshot. Metric points are not
    affected. Idempotent; runs with housekeeping."""
    now = now or _now()
    cutoff = now - timedelta(hours=config.SNAPSHOT_FULL_RESOLUTION_HOURS)
    doomed: list[int] = []
    deleted = 0
    with get_db() as connection:
        current = {r[0] for r in connection.execute(select(monitor_latest.c.snapshot_id))}
        rows = connection.execution_options(yield_per=5000).execute(
            select(monitor_snapshots.c.id, monitor_snapshots.c.system_id, monitor_snapshots.c.monitor,
                   monitor_snapshots.c.captured_at, monitor_snapshots.c.health)
            .where(monitor_snapshots.c.captured_at < cutoff)
            .order_by(monitor_snapshots.c.system_id, monitor_snapshots.c.monitor, monitor_snapshots.c.captured_at,
                      monitor_snapshots.c.id))
        group, hour, health = None, None, None
        for sid, system_id, monitor, captured, h in rows:
            captured = datetime.fromisoformat(_iso(captured))
            bucket = captured.replace(minute=0, second=0, microsecond=0)
            if (system_id, monitor) != group:
                group, hour, health = (system_id, monitor), bucket, h
                continue
            if bucket != hour or h != health or sid in current:
                hour, health = bucket, h
                continue
            doomed.append(sid)
    for i in range(0, len(doomed), 5000):
        with get_db() as connection:
            deleted += connection.execute(delete(monitor_snapshots).where(monitor_snapshots.c.id.in_(doomed[i:i + 5000]))).rowcount
    return deleted


def _health(levels: list[str]) -> str:
    order = ["healthy", "warning", "critical"]
    return max(levels, key=order.index) if levels else "healthy"


# ------------------------------------------------------------------------------- builders
def build_availability(instances: list[dict]) -> tuple[dict, dict, str]:
    """instances: [{hostname, instance_nr, features, status, processes:[{name, description, status, textstatus,
    elapsed, pid}], error}]"""
    for inst in instances:
        inst["status"] = STATUS_COLOR.get(str(inst.get("status", "")).upper().replace("SAPCONTROL-", ""), inst.get("status") or "gray")
        for p in inst.get("processes", []):
            p["status"] = STATUS_COLOR.get(str(p.get("status", "")).upper().replace("SAPCONTROL-", ""), p.get("status") or "gray")
    feats = lambda i: set((i.get("features") or "").upper().split("|"))  # noqa: E731
    running = [i for i in instances if i["status"] == "green"]
    down = [i for i in instances if i["status"] in ("red", "gray")]
    degraded = [i for i in instances if i["status"] == "yellow"]
    central = [i for i in instances if feats(i) & {"MESSAGESERVER", "ENQUE", "ENQREP"}] or instances
    app_servers = [i for i in instances if feats(i) & {"ABAP", "J2EE"}] or instances
    system_up = any(i["status"] in ("green", "yellow") for i in central) and any(
        i["status"] in ("green", "yellow") for i in app_servers)
    summary = {"instances": len(instances), "running": len(running), "degraded": len(degraded), "down": len(down),
               "system_up": system_up}
    metrics = {"availability.system_up": 1.0 if system_up else 0.0,
               "availability.instances_down": float(len(down) + len(degraded))}
    health = "critical" if not system_up else "warning" if down or degraded else "healthy"
    return {"summary": summary, "instances": instances}, metrics, health


def build_workprocesses(per_instance: list[dict]) -> tuple[dict, dict, str]:
    """per_instance: [{instance, workprocesses:[{no,type,pid,status,reason,err,time_s,program,client,user,action,table}]}]"""
    by_type: dict[str, dict] = defaultdict(lambda: {"total": 0, "busy": 0, "stopped": 0})
    long_running, broken = [], []
    errors = 0
    for inst in per_instance:
        for wp in inst["workprocesses"]:
            t = (wp.get("type") or "?").upper()
            status = (wp.get("status") or "").strip().lower()
            entry = by_type[t]
            entry["total"] += 1
            if status in WP_BUSY:
                entry["busy"] += 1
                if (wp.get("time_s") or 0) >= config.WP_LONG_RUNNING_SECONDS:
                    long_running.append({**wp, "instance": inst["instance"]})
            if status in WP_BROKEN:
                entry["stopped"] += 1
                broken.append({**wp, "instance": inst["instance"]})
            errors += int(wp.get("err") or 0)
    for entry in by_type.values():
        entry["busy_pct"] = round(entry["busy"] / entry["total"] * 100, 1) if entry["total"] else 0.0
    long_running.sort(key=lambda w: -(w.get("time_s") or 0))
    metrics = {"wp.long_running": float(len(long_running)), "wp.stopped": float(len(broken))}
    if "DIA" in by_type:
        metrics["wp.dia_busy_pct"] = by_type["DIA"]["busy_pct"]
    if "BTC" in by_type:
        metrics["wp.btc_busy_pct"] = by_type["BTC"]["busy_pct"]
    data = {"summary": {"by_type": dict(by_type), "long_running": len(long_running), "stopped": len(broken),
                        "restarts": errors, "threshold_s": config.WP_LONG_RUNNING_SECONDS},
            "long_running": long_running[:50], "stopped": broken[:50], "instances": per_instance}
    dia = by_type.get("DIA", {}).get("busy_pct", 0)
    health = "critical" if dia >= 95 or len(broken) >= 3 else "warning" if dia >= 80 or long_running or broken else "healthy"
    return data, metrics, health


def build_dumps(rows: list[dict], now: datetime | None = None) -> tuple[dict, dict, str]:
    now = now or _now()
    rows = [r for r in rows if r.get("at")]
    rows.sort(key=lambda r: r["at"], reverse=True)
    last_hour = [r for r in rows if r["at"] >= now - timedelta(hours=1)]
    last_day = [r for r in rows if r["at"] >= now - timedelta(hours=24)]
    top = Counter(r.get("error") or "unknown" for r in last_day).most_common(10)
    users = Counter(r.get("user") or "?" for r in last_day).most_common(10)
    hourly = Counter(r["at"].replace(minute=0, second=0, microsecond=0) for r in last_day)
    data = {"summary": {"last_hour": len(last_hour), "last_24h": len(last_day), "distinct_errors": len(top),
                        "top_errors": [{"error": e, "count": c} for e, c in top],
                        "top_users": [{"user": u, "count": c} for u, c in users]},
            "hourly": [{"hour": h.isoformat(), "count": c} for h, c in sorted(hourly.items())],
            "recent": [{**r, "at": r["at"].isoformat()} for r in last_day[:100]]}
    metrics = {"dumps.last_hour": float(len(last_hour)), "dumps.last_24h": float(len(last_day))}
    health = "critical" if len(last_hour) >= 100 else "warning" if len(last_hour) >= 20 else "healthy"
    return data, metrics, health


SYSLOG_ERROR_WORDS = ("ERROR", "FAIL", "TERMINAT", "ABORT", "CANCEL", "DEADLOCK", "TIMEOUT", "CRITICAL")


def build_syslog(rows: list[dict], now: datetime | None = None) -> tuple[dict, dict, str]:
    now = now or _now()
    rows = [r for r in rows if r.get("at")]
    for r in rows:
        sev = (r.get("severity") or "").lower()
        r["is_error"] = sev in ("error", "e", "high", "1", "critical", "x") or (
            not sev and any(w in (r.get("text") or "").upper() for w in SYSLOG_ERROR_WORDS))
    rows.sort(key=lambda r: r["at"], reverse=True)
    last_hour = [r for r in rows if r["at"] >= now - timedelta(hours=1)]
    last_day = [r for r in rows if r["at"] >= now - timedelta(hours=24)]
    errors_hour = sum(1 for r in last_hour if r["is_error"])
    top = Counter(r.get("msgno") or "?" for r in last_day if r["is_error"]).most_common(10)
    data = {"summary": {"entries_24h": len(last_day), "errors_last_hour": errors_hour,
                        "errors_24h": sum(1 for r in last_day if r["is_error"]),
                        "top_messages": [{"msgno": m, "count": c} for m, c in top]},
            "recent": [{**r, "at": r["at"].isoformat()} for r in last_day[:150]]}
    metrics = {"syslog.errors_last_hour": float(errors_hour)}
    health = "critical" if errors_hour >= 50 else "warning" if errors_hour >= 10 else "healthy"
    return data, metrics, health


def build_updates(rows: list[dict]) -> tuple[dict, dict, str]:
    rows = sorted(rows, key=lambda r: r.get("at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    data = {"summary": {"errors": len(rows), "users": len({r.get("user") for r in rows}),
                        "by_tcode": [{"tcode": t, "count": c} for t, c in Counter(r.get("tcode") or "?" for r in rows).most_common(10)]},
            "recent": [{**r, "at": r["at"].isoformat() if r.get("at") else None} for r in rows[:100]]}
    metrics = {"updates.errors": float(len(rows))}
    health = "critical" if len(rows) >= 10 else "warning" if rows else "healthy"
    return data, metrics, health


def build_trfc(rows: list[dict]) -> tuple[dict, dict, str]:
    errors = [r for r in rows if (r.get("state") or "").upper() in TRFC_ERRORS]
    by_dest = Counter((r.get("dest") or "?", (r.get("state") or "?").upper()) for r in errors)
    errors.sort(key=lambda r: r.get("at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    data = {"summary": {"errors": len(errors), "destinations": len({d for d, _ in by_dest}),
                        "by_destination": [{"dest": d, "state": s, "count": c} for (d, s), c in by_dest.most_common(20)]},
            "recent": [{**r, "at": r["at"].isoformat() if r.get("at") else None} for r in errors[:100]]}
    metrics = {"trfc.errors": float(len(errors))}
    health = "critical" if len(errors) >= 50 else "warning" if errors else "healthy"
    return data, metrics, health


def build_qrfc(rows: list[dict]) -> tuple[dict, dict, str]:
    """rows: one per queue: {direction, qname, dest, state, entries, oldest(datetime|None)}"""
    blocked = [r for r in rows if (r.get("state") or "").upper() in QRFC_BLOCKED]
    data = {"summary": {"queues": len(rows), "blocked_queues": len(blocked),
                        "entries": sum(int(r.get("entries") or 0) for r in rows),
                        "blocked_entries": sum(int(r.get("entries") or 0) for r in blocked)},
            "blocked": [{**r, "oldest": r["oldest"].isoformat() if r.get("oldest") else None}
                        for r in sorted(blocked, key=lambda r: -int(r.get("entries") or 0))][:100],
            "largest": [{**r, "oldest": r["oldest"].isoformat() if r.get("oldest") else None}
                        for r in sorted(rows, key=lambda r: -int(r.get("entries") or 0))][:20]}
    metrics = {"qrfc.blocked_queues": float(len(blocked))}
    health = "critical" if len(blocked) >= 10 else "warning" if blocked else "healthy"
    return data, metrics, health


def build_idocs(error_rows: list[dict], total_24h: int | None, now: datetime | None = None) -> tuple[dict, dict, str]:
    now = now or _now()
    recent = [r for r in error_rows if r.get("at") and r["at"] >= now - timedelta(hours=24)]
    by_status = Counter(r["status"] for r in recent)
    by_type = Counter((r.get("mestyp") or "?") for r in recent)
    recent.sort(key=lambda r: r["at"], reverse=True)
    data = {"summary": {"errors_24h": len(recent), "total_24h": total_24h,
                        "by_status": [{"status": s, "text": IDOC_ERROR_STATUSES.get(s, ""), "count": c} for s, c in by_status.most_common()],
                        "by_message_type": [{"mestyp": m, "count": c} for m, c in by_type.most_common(15)]},
            "recent": [{**r, "at": r["at"].isoformat(), "status_text": IDOC_ERROR_STATUSES.get(r["status"], "")} for r in recent[:100]]}
    metrics = {"idoc.errors_24h": float(len(recent))}
    health = "critical" if len(recent) >= 100 else "warning" if len(recent) >= 10 else "healthy"
    return data, metrics, health


def build_hana(parts: dict, now: datetime | None = None) -> tuple[dict, dict, str]:
    """parts: memory{used, limit} (bytes), cpu_pct, disk{used,total}, alerts[], backups{type: datetime},
    replication[], services[], version"""
    now = now or _now()
    metrics: dict[str, float] = {}
    levels = []
    mem = parts.get("memory")
    if mem and mem.get("limit"):
        mem["pct"] = round(mem["used"] / mem["limit"] * 100, 1)
        metrics["hana.memory_used_pct"] = mem["pct"]
        levels.append("critical" if mem["pct"] >= 95 else "warning" if mem["pct"] >= 85 else "healthy")
    if parts.get("cpu_pct") is not None:
        metrics["hana.cpu_pct"] = float(parts["cpu_pct"])
    active = parts.get("active_statements") or {}
    if "count" in active:
        metrics["hana.active_statements"] = float(active.get("count") or 0)
        metrics["hana.long_running_statements"] = float(active.get("long_running") or 0)
        metrics["hana.max_statement_duration_s"] = float(active.get("max_duration_s") or 0)
    threads = parts.get("service_threads") or {}
    if "waiting" in threads:
        metrics["hana.waiting_threads"] = float(threads.get("waiting") or 0)
    expensive = parts.get("expensive_statements_1h") or {}
    if "count" in expensive:
        metrics["hana.expensive_statements_1h"] = float(expensive.get("count") or 0)
        metrics["hana.expensive_statement_max_duration_s"] = float(expensive.get("max_duration_s") or 0)
    disk = parts.get("disk")
    if disk and disk.get("total"):
        disk["pct"] = round(disk["used"] / disk["total"] * 100, 1)
        metrics["hana.disk_used_pct"] = disk["pct"]
    alerts = parts.get("alerts") or []
    high = [a for a in alerts if int(a.get("rating") or 0) >= 4]
    metrics["hana.alerts_high"] = float(len(high))
    backups = {}
    for kind, at in (parts.get("backups") or {}).items():
        age = round((now - at).total_seconds() / 3600, 1) if at else None
        backups[kind] = {"at": at.isoformat() if at else None, "age_h": age}
    data_bkp = backups.get("complete data backup") or backups.get("data snapshot")
    if data_bkp and data_bkp["age_h"] is not None:
        metrics["hana.data_backup_age_h"] = data_bkp["age_h"]
        levels.append("critical" if data_bkp["age_h"] >= 50 else "warning" if data_bkp["age_h"] >= 26 else "healthy")
    elif "backups" in parts:
        levels.append("warning")  # no successful data backup found
    log_bkp = backups.get("log backup")
    if log_bkp and log_bkp["age_h"] is not None:
        metrics["hana.log_backup_age_h"] = log_bkp["age_h"]
    replication = parts.get("replication") or []
    if replication:
        ok = all((r.get("status") or "").upper() == "ACTIVE" for r in replication)
        metrics["hana.replication_ok"] = 1.0 if ok else 0.0
        levels.append("healthy" if ok else "critical")
    if parts.get("log_disk"):
        metrics["hana.log_disk_used_pct"] = parts["log_disk"]["pct"]
        levels.append("critical" if parts["log_disk"]["pct"] >= 90 else "warning" if parts["log_disk"]["pct"] >= 80 else "healthy")
    if parts.get("blocked_transactions") is not None:
        metrics["hana.blocked_transactions"] = float(parts["blocked_transactions"]["count"])
    if parts.get("oom_events_24h") is not None:
        metrics["hana.oom_events_24h"] = float(parts["oom_events_24h"])
        if parts["oom_events_24h"]:
            levels.append("warning")
    services = parts.get("services") or []
    if "services" in parts:
        inactive = sum(1 for s in services if (s.get("active") or "").upper() not in ("YES", "TRUE", "1"))
        metrics["hana.services_inactive"] = float(inactive)
        if inactive:
            levels.append("critical")
    levels.append("warning" if high else "healthy")
    data = {"version": parts.get("version"), "memory": mem, "cpu_pct": parts.get("cpu_pct"), "disk": disk,
            "alerts": sorted(alerts, key=lambda a: -int(a.get("rating") or 0))[:50], "backups": backups,
            "replication": replication, "services": services, "active_statements": active,
            "log_disk": parts.get("log_disk"), "blocked_transactions": parts.get("blocked_transactions"),
            "oom_events_24h": parts.get("oom_events_24h"),
            "service_threads": threads, "expensive_statements_1h": expensive}
    return data, metrics, _health(levels)


# MTE names (CCMS, instance context) whose value becomes a metric. Values are averaged over the instances that report them.
CCMS_METRICS = {"responsetime": ("abap.dialog_response_ms", "avg"), "usersloggedin": ("abap.users_logged_on", "sum"),
                "frontendresponsetime": ("abap.frontend_response_ms", "avg")}


def build_abap_live(live: list[dict], now: datetime | None = None) -> tuple[dict, dict, str]:
    """Near-real-time ABAP signals from sapstartsrv: dispatcher queues, CCMS (instance shared memory), enqueue server.

    CCMS colours come from the monitoring tree's current values. Open alerts (GetAlerts) are shown for context only: they
    stay open until someone completes them in RZ20, so counting them would report problems long fixed."""
    now = now or _now()
    metrics: dict[str, float] = {}
    levels: list[str] = []
    queues: dict[str, dict] = {}
    for inst in live:
        for q in inst.get("queues") or []:
            kind = q["type"].upper().split("/")[-1]
            agg = queues.setdefault(kind, {"type": q["type"], "now": 0, "high": 0, "max": q["max"], "instances": 0})
            agg["now"] += q["now"]
            agg["high"] = max(agg["high"], q["high"])
            agg["instances"] += 1
    for kind, metric in (("DIA", "abap.queue_dia"), ("UPD", "abap.queue_upd"), ("BTC", "abap.queue_btc")):
        if kind in queues:
            metrics[metric] = float(queues[kind]["now"])
    if queues.get("DIA", {}).get("now", 0) >= 20:
        levels.append("warning")

    values: dict[str, list[float]] = {}
    red, yellow, nodes = [], [], []
    for inst in live:
        for n in inst.get("ccms_tree") or []:
            if not n["leaf"]:
                continue
            nodes.append({**n, "instance": inst["instance"]})
            key = n["name"].replace(" ", "").replace("_", "").lower()
            if key in CCMS_METRICS and n["value"] is not None:
                values.setdefault(key, []).append(n["value"])
            if n["color"] == "RED":
                red.append({**n, "instance": inst["instance"]})
            elif n["color"] == "YELLOW":
                yellow.append({**n, "instance": inst["instance"]})
    # ST02 buffers (CCMS 'Buffers' subtree): lowest hit ratio of any buffer, swaps per instance (highest instance)
    hit_ratios, swaps_by_instance, buffers = [], {}, []
    for n in nodes:
        if "\\buffer" not in n["path"].lower() or n["value"] is None:
            continue
        key = n["name"].replace(" ", "").lower()
        if key == "hitratio":
            hit_ratios.append(n["value"])
            buffers.append({"instance": n["instance"], "buffer": n["path"].split("\\")[-2], "hit_ratio": n["value"]})
        elif key in ("swap", "swaps"):
            swaps_by_instance[n["instance"]] = swaps_by_instance.get(n["instance"], 0.0) + n["value"]
    if hit_ratios:
        metrics["abap.buffer_hitratio_min"] = min(hit_ratios)
    if swaps_by_instance:
        metrics["abap.buffer_swaps"] = max(swaps_by_instance.values())
    for key, vals in values.items():
        metric, how = CCMS_METRICS[key]
        metrics[metric] = round(sum(vals) / len(vals), 2) if how == "avg" else float(sum(vals))
    if any(inst.get("ccms_tree") is not None for inst in live):
        metrics["ccms.red_now"] = float(len(red))
        metrics["ccms.yellow_now"] = float(len(yellow))
        levels.append("critical" if red else "warning" if yellow else "healthy")
    open_alerts = [{**a, "instance": inst["instance"]} for inst in live for a in inst.get("ccms_alerts") or []]

    enqueue = next((inst["enqueue"] for inst in live if inst.get("enqueue")), None)
    locks = next((inst["locks"] for inst in live if inst.get("locks") is not None), None)
    if enqueue and enqueue.get("table_used_pct") is not None:
        metrics["abap.enq_table_used_pct"] = enqueue["table_used_pct"]
        levels.append("critical" if enqueue["table_used_pct"] >= 90 else "warning" if enqueue["table_used_pct"] >= 70 else "healthy")
    if locks is not None:
        ages = [lock["age_minutes"] for lock in locks if lock["age_minutes"] is not None]
        metrics["abap.locks"] = float(len(locks))
        metrics["abap.locks_oldest_min"] = float(max(ages)) if ages else 0.0
        metrics["abap.locks_long"] = float(sum(1 for a in ages if a >= config.LOCK_WARNING_MINUTES))
    data = {
        "summary": {"queues": queues, "ccms_red": len(red), "ccms_yellow": len(yellow), "open_alerts": len(open_alerts),
                    "locks": len(locks) if locks is not None else None, "enqueue": enqueue,
                    "dialog_response_ms": metrics.get("abap.dialog_response_ms"),
                    "users_logged_on": metrics.get("abap.users_logged_on")},
        "ccms_red": red[:100], "ccms_yellow": yellow[:100],
        "open_alerts": sorted(open_alerts, key=lambda a: a.get("time") or "", reverse=True)[:100],
        "ccms_values": [n for n in nodes if n["value"] is not None][:400],
        "buffers": sorted(buffers, key=lambda b: b["hit_ratio"])[:30],
        "locks_longest": sorted((lk for lk in (locks or []) if lk["age_minutes"] is not None),
                                key=lambda lk: -lk["age_minutes"])[:50],
        "instances": [inst["instance"] for inst in live],
    }
    return data, metrics, _health(levels)


# ------------------------------------------------------------------------------- Basis daily checks
STANDARD_USERS = ("SAP*", "DDIC", "TMSADM", "EARLYWATCH", "SAPCPIC")
PROTECTED_ENVIRONMENTS = ("PRD", "PRE", "QAS")


def _num(value) -> int | None:
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _sap_date(value) -> str | None:
    """'20261110' -> '2026-11-10'; '00000000' / empty -> None."""
    text = str(value or "").strip().replace("-", "")
    if len(text) != 8 or not text.isdigit() or not text.strip("0"):
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def build_number_ranges(rows: list[dict]) -> tuple[dict, dict, str]:
    """SNRO: fill level of internal number ranges. A full range stops document creation."""
    ranges = []
    for r in rows:
        r = {str(k).upper(): v for k, v in r.items()}
        lo, hi, level = _num(r.get("FROMNUMBER")), _num(r.get("TONUMBER")), _num(r.get("NRLEVEL"))
        if lo is None or hi is None or level is None or hi <= lo:
            continue
        pct = round(max(0.0, min(100.0, (level - lo) / (hi - lo) * 100)), 2)
        ranges.append({"client": str(r.get("CLIENT") or "").strip() or None, "object": str(r.get("OBJECT") or "").strip(),
                       "subobject": str(r.get("SUBOBJECT") or "").strip() or None, "range": str(r.get("NRRANGENR") or "").strip(),
                       "year": str(r.get("TOYEAR") or "").strip().lstrip("0") or None, "used_pct": pct,
                       "remaining": hi - level})
    ranges.sort(key=lambda x: -x["used_pct"])
    top = ranges[0]["used_pct"] if ranges else 0.0
    metrics = {"numrange.max_used_pct": float(top), "numrange.over_80": float(sum(1 for x in ranges if x["used_pct"] >= 80))}
    health = "critical" if top >= 90 else "warning" if top >= 80 else "healthy"
    return {"summary": {"ranges_reported": len(ranges), "max_used_pct": top, "over_80": int(metrics["numrange.over_80"])},
            "ranges": [x for x in ranges if x["used_pct"] >= 50][:200]}, metrics, health


def build_applog(rows: list[dict]) -> tuple[dict, dict, str]:
    rows = sorted(rows, key=lambda r: -r["logs"])
    total = sum(r["logs"] for r in rows)
    return ({"summary": {"error_logs_24h": total, "objects": len(rows)}, "by_object": rows[:100]},
            {"applog.error_logs_24h": float(total)}, "warning" if total >= 100 else "healthy")


def build_batch_input(rows: list[dict]) -> tuple[dict, dict, str]:
    rows = sorted(rows, key=lambda r: r.get("created") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    view = [{**r, "created": r["created"].isoformat() if r.get("created") else None} for r in rows]
    return ({"summary": {"error_sessions_7d": len(rows)}, "sessions": view[:200]},
            {"batchinput.error_sessions_7d": float(len(rows))}, "warning" if rows else "healthy")


def build_security(parts: dict, environment: str | None) -> tuple[dict, dict, str]:
    """SCC4 client settings and the standard users (SU01), following the usual SAP security baseline:
    production clients closed for changes, SAP* present and locked in every client, EARLYWATCH locked."""
    metrics: dict[str, float] = {}
    findings: list[dict] = []
    clients = []
    for c in parts.get("clients") or []:
        c = {str(k).upper(): v for k, v in c.items()}
        mandt = str(c.get("MANDT") or "").strip()
        if not mandt:
            continue
        cor, cross = str(c.get("CCCORACTIV") or "").strip(), str(c.get("CCNOCLIIND") or "").strip()
        # CCCORACTIV '2' = no changes allowed; CCNOCLIIND '2'/'3' = no changes to cross-client objects
        open_client = cor != "2" or cross not in ("2", "3")
        clients.append({"client": mandt, "text": str(c.get("MTEXT") or "").strip(), "role": str(c.get("CCCATEGORY") or "").strip(),
                        "client_specific_changes": {"": "allowed without recording", "1": "recorded in transports",
                                                    "2": "not allowed", "3": "allowed, no transport"}.get(cor, cor),
                        "cross_client_changes": {"": "allowed", "1": "repository blocked", "2": "customizing blocked",
                                                 "3": "not allowed"}.get(cross, cross), "open": open_client})
    business = [c for c in clients if c["client"] not in ("000", "001", "066")]
    if parts.get("clients") is not None and (environment or "").upper() in PROTECTED_ENVIRONMENTS:
        opened = [c for c in business if c["open"]]
        metrics["security.prod_clients_open"] = float(len(opened))
        findings += [{"severity": "warning", "check": "Client open for changes (SCC4)",
                      "detail": f"Client {c['client']}: client-specific {c['client_specific_changes']}, cross-client {c['cross_client_changes']}"}
                     for c in opened]
    users = []
    users_by_client: dict[str, dict] = {}
    for u in parts.get("critical_users") or []:
        u = {str(k).upper(): v for k, v in u.items()}
        flag = int(str(u.get("UFLAG") or 0).strip() or 0)
        entry = {"client": str(u.get("MANDT") or "").strip(), "user": str(u.get("BNAME") or "").strip(), "locked": flag != 0,
                 "failed_logons": int(str(u.get("LOCNT") or 0).strip() or 0), "type": str(u.get("USTYP") or "").strip(),
                 "last_logon": _sap_date(u.get("TRDAT"))}
        users.append(entry)
        users_by_client.setdefault(entry["client"], {})[entry["user"]] = entry
    if parts.get("critical_users") is not None:
        risk = 0
        for client in sorted({c["client"] for c in clients} or users_by_client):
            if client == "066":
                continue
            star = users_by_client.get(client, {}).get("SAP*")
            if star is None:
                risk += 1
                findings.append({"severity": "warning", "check": "SAP* missing (SU01)",
                                 "detail": f"Client {client}: without a SAP* record the kernel accepts the default password "
                                           "unless login/no_automatic_user_sapstar = 1"})
            elif not star["locked"]:
                risk += 1
                findings.append({"severity": "warning", "check": "SAP* not locked (SU01)", "detail": f"Client {client}"})
            ew = users_by_client.get(client, {}).get("EARLYWATCH")
            if ew is not None and not ew["locked"]:
                findings.append({"severity": "info", "check": "EARLYWATCH not locked", "detail": f"Client {client}"})
        metrics["security.sapstar_risk"] = float(risk)
    if parts.get("failed_logon_locks") is not None:
        locked = sum(int(str({str(k).upper(): v for k, v in r.items()}.get("LOCKED") or 0)) for r in parts["failed_logon_locks"])
        metrics["security.users_locked_failed_logon"] = float(locked)
    level = "warning" if any(f["severity"] == "warning" for f in findings) else "healthy"
    return ({"summary": {"findings": len(findings), "clients": len(clients), "open_clients": sum(1 for c in business if c["open"]),
                         "environment": environment}, "findings": findings, "clients": clients, "standard_users": users},
            metrics, level)


def build_spool(parts: dict) -> tuple[dict, dict, str]:
    total = parts.get("requests")
    metrics = {"spool.requests": float(total)} if total is not None else {}
    return {"summary": {"requests": total}}, metrics, "healthy"


# ------------------------------------------------------------------------------- upload fallbacks
def _dt(row: dict, zone, date_aliases, time_aliases):
    return parse_sap_datetime(pick(row, *date_aliases), pick(row, *time_aliases), zone)


def parse_upload(monitor: str, raw: bytes, zone) -> tuple[dict, dict, str]:
    """Build a monitor from an SAP GUI export. Returns (data, metrics, health)."""
    rows = read_rows(raw)
    if monitor == "dumps":
        require_columns(rows, {"date": ("Date", "DATUM"), "runtime error": ("Runtime Error", "Runtime Errors", "Error", "ERRID")})
        return build_dumps([{"at": _dt(r, zone, ("Date", "DATUM"), ("Time", "UZEIT")),
                             "error": pick(r, "Runtime Error", "Runtime Errors", "Error", "ERRID"),
                             "user": pick(r, "User", "UNAME"), "host": pick(r, "Application Server", "Host", "AHOST"),
                             "client": pick(r, "Client", "Cl.", "MANDT"), "program": pick(r, "Program", "Terminated Program")}
                            for r in rows])
    if monitor == "syslog":
        require_columns(rows, {"time": ("Time", "Date"), "text": ("Message Text", "Text", "Message")})
        return build_syslog([{"at": _dt(r, zone, ("Date",), ("Time",)) or parse_sap_datetime(pick(r, "Time"), "", zone),
                              "severity": pick(r, "Severity", "Priority", "Prio"), "msgno": pick(r, "MNo", "Message ID", "Msg. No."),
                              "text": pick(r, "Message Text", "Text", "Message"), "user": pick(r, "User"),
                              "client": pick(r, "Client", "Cl."), "program": pick(r, "Program", "Tcode", "Transaction"),
                              "instance": pick(r, "Instance", "Server")} for r in rows])
    if monitor == "updates":
        require_columns(rows, {"user": ("User", "VBUSR"), "status": ("Status", "State", "VBRC")})
        errs = [r for r in rows if pick(r, "Status", "State", "VBRC").strip().upper() not in ("", "0", "INIT", "RUN", "AUTO", "V2 PROCESSED")]
        return build_updates([{"at": _dt(r, zone, ("Date",), ("Time",)), "user": pick(r, "User", "VBUSR"),
                               "tcode": pick(r, "Tcode", "Transaction", "TCode", "VBTCODE"), "report": pick(r, "Report", "Program", "VBREPORT"),
                               "state": pick(r, "Status", "State", "VBRC"), "client": pick(r, "Client", "Cl.")} for r in errs])
    if monitor == "trfc":
        require_columns(rows, {"destination": ("Target System", "Destination", "ARFCDEST"), "status": ("Status Text", "Status", "ARFCSTATE")})
        out = []
        for r in rows:
            status_text = pick(r, "Status Text", "Status", "ARFCSTATE")
            state = next((s for s in TRFC_ERRORS if s in status_text.upper()), None) or (
                "SYSFAIL" if re.search(r"error|fail|dump|abort", status_text, re.I) else status_text.upper()[:20])
            out.append({"dest": pick(r, "Target System", "Destination", "ARFCDEST"), "state": state, "text": status_text,
                        "function": pick(r, "Function Module", "ARFCFNAM"), "user": pick(r, "Caller", "User", "ARFCUSER"),
                        "at": _dt(r, zone, ("Date", "ARFCDATUM"), ("Time", "ARFCUZEIT"))})
        return build_trfc(out)
    if monitor == "qrfc":
        require_columns(rows, {"queue": ("Queue Name", "QNAME"), "status": ("Status", "QSTATE")})
        return build_qrfc([{"direction": pick(r, "Direction") or "out", "qname": pick(r, "Queue Name", "QNAME"),
                            "dest": pick(r, "Destination", "DEST"), "state": pick(r, "Status", "QSTATE").upper(),
                            "entries": int(parse_number(pick(r, "Entries", "Entry", "Count")) or 0),
                            "oldest": _dt(r, zone, ("1st Date", "First Date", "Date"), ("1st Time", "First Time", "Time"))}
                           for r in rows])
    if monitor == "idocs":
        require_columns(rows, {"status": ("Status", "STATUS"), "idoc number": ("IDoc number", "IDoc Number", "DOCNUM", "IDoc")})
        all_rows = [{"docnum": pick(r, "IDoc number", "IDoc Number", "DOCNUM", "IDoc").lstrip("0"),
                     "status": pick(r, "Status", "STATUS").zfill(2)[:2], "mestyp": pick(r, "Message Type", "MESTYP"),
                     "direction": pick(r, "Direction", "DIRECT"), "partner": pick(r, "Partner", "Partner number", "RCVPRN", "SNDPRN"),
                     "at": _dt(r, zone, ("Created on", "Created On", "CREDAT", "Date"), ("Time", "CRETIM", "Created at"))}
                    for r in rows]
        recent = [r for r in all_rows if r["at"] and r["at"] >= _now() - timedelta(hours=24)]
        return build_idocs([r for r in all_rows if r["status"] in IDOC_ERROR_STATUSES], len(recent))
    raise InputFormatError(f"Uploads are not supported for monitor '{monitor}'")

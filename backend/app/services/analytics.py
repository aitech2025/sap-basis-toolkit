"""Historical telemetry analytics for SAP Basis Sentinel.

The query path is intentionally portable: TimescaleDB accelerates the indexed
time-range scan, while aggregation remains SQL-dialect independent for SQLite
and vanilla PostgreSQL deployments.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..db import get_db, metric_points, sap_systems

METRICS = [
    {"key": "availability.system_up", "label": "System availability", "category": "Availability", "unit": "%", "description": "1 = SAP system available; 0 = unavailable", "format": "percent"},
    {"key": "availability.instances_down", "label": "Instances down", "category": "Availability", "unit": "instances", "description": "Number of SAP instances reported down", "format": "number"},
    {"key": "wp.dia_busy_pct", "label": "DIA work process busy", "category": "Work processes", "unit": "%", "description": "Dialog work-process busy percentage", "format": "percent"},
    {"key": "wp.btc_busy_pct", "label": "BTC work process busy", "category": "Work processes", "unit": "%", "description": "Background work-process busy percentage", "format": "percent"},
    {"key": "wp.long_running", "label": "Long-running work processes", "category": "Work processes", "unit": "processes", "description": "Work processes exceeding the long-running threshold", "format": "number"},
    {"key": "wp.stopped", "label": "Stopped work processes", "category": "Work processes", "unit": "processes", "description": "Stopped or broken work processes", "format": "number"},
    {"key": "dumps.last_hour", "label": "ABAP dumps (last hour)", "category": "ABAP errors", "unit": "dumps", "description": "Short dumps observed in the previous hour", "format": "number"},
    {"key": "dumps.last_24h", "label": "ABAP dumps (24 hours)", "category": "ABAP errors", "unit": "dumps", "description": "Short dumps observed in the previous 24 hours", "format": "number"},
    {"key": "syslog.errors_last_hour", "label": "System log errors", "category": "ABAP errors", "unit": "errors", "description": "System log errors observed in the previous hour", "format": "number"},
    {"key": "updates.errors", "label": "Update errors", "category": "ABAP errors", "unit": "errors", "description": "Failed update requests in the latest snapshot", "format": "number"},
    {"key": "trfc.errors", "label": "tRFC errors", "category": "Interfaces", "unit": "errors", "description": "Failed transactional RFC units", "format": "number"},
    {"key": "qrfc.blocked_queues", "label": "Blocked qRFC queues", "category": "Interfaces", "unit": "queues", "description": "Blocked outbound or inbound queues", "format": "number"},
    {"key": "idoc.errors_24h", "label": "IDoc errors (24 hours)", "category": "Interfaces", "unit": "IDocs", "description": "IDocs in error status over the previous 24 hours", "format": "number"},
    {"key": "hana.memory_used_pct", "label": "HANA memory used", "category": "HANA", "unit": "%", "description": "HANA memory utilisation", "format": "percent"},
    {"key": "hana.cpu_pct", "label": "HANA CPU", "category": "HANA", "unit": "%", "description": "HANA CPU utilisation", "format": "percent"},
    {"key": "hana.disk_used_pct", "label": "HANA disk used", "category": "HANA", "unit": "%", "description": "HANA data-volume utilisation", "format": "percent"},
    {"key": "hana.alerts_high", "label": "HANA high alerts", "category": "HANA", "unit": "alerts", "description": "High-priority HANA alerts", "format": "number"},
    {"key": "hana.data_backup_age_h", "label": "Data backup age", "category": "HANA", "unit": "hours", "description": "Age of the latest complete data backup", "format": "hours"},
    {"key": "hana.log_backup_age_h", "label": "Log backup age", "category": "HANA", "unit": "hours", "description": "Age of the latest log backup", "format": "hours"},
    {"key": "hana.replication_ok", "label": "HANA replication", "category": "HANA", "unit": "status", "description": "1 = replication active; 0 = replication unhealthy", "format": "status"},
]
METRIC_MAP = {item["key"]: item for item in METRICS}
AGGREGATIONS = ("avg", "min", "max", "last")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value else None


def catalogue() -> list[dict]:
    return METRICS


def summary(hours: int = 24, landscape: str | None = None) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = select(
        metric_points.c.system_id, metric_points.c.metric, metric_points.c.ts
    ).join(sap_systems, sap_systems.c.id == metric_points.c.system_id).where(
        metric_points.c.ts >= since
    )
    if landscape:
        query = query.where(sap_systems.c.landscape == landscape)
    with get_db() as connection:
        rows = connection.execute(query).all()
        total_systems = connection.execute(
            select(sap_systems.c.id).where(sap_systems.c.enabled.is_(True),
                *([sap_systems.c.landscape == landscape] if landscape else []))
        ).all()
    systems = {row.system_id for row in rows}
    metrics = {row.metric for row in rows}
    newest = max((_aware(row.ts) for row in rows), default=None)
    return {
        "window_hours": hours,
        "systems_total": len(total_systems),
        "systems_reporting": len(systems),
        "samples": len(rows),
        "metrics_reporting": len(metrics),
        "last_sample_at": _iso(newest),
        "metrics": sorted(metrics),
    }


def series(metric: str, hours: int = 24, bucket_seconds: int = 300,
           aggregation: str = "avg", system_id: int | None = None,
           landscape: str | None = None) -> dict:
    if metric not in METRIC_MAP:
        raise ValueError(f"Unknown metric '{metric}'")
    if aggregation not in AGGREGATIONS:
        raise ValueError(f"aggregation must be one of {', '.join(AGGREGATIONS)}")
    if bucket_seconds not in (60, 300, 900, 1800, 3600, 21600, 86400):
        raise ValueError("bucket_seconds must be one of 60, 300, 900, 1800, 3600, 21600, 86400")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = select(
        metric_points.c.system_id, sap_systems.c.sid, sap_systems.c.name,
        sap_systems.c.landscape, sap_systems.c.environment, metric_points.c.value,
        metric_points.c.ts,
    ).join(sap_systems, sap_systems.c.id == metric_points.c.system_id).where(
        metric_points.c.metric == metric, metric_points.c.ts >= since
    ).order_by(metric_points.c.ts)
    if system_id is not None:
        query = query.where(metric_points.c.system_id == system_id)
    if landscape:
        query = query.where(sap_systems.c.landscape == landscape)

    with get_db() as connection:
        rows = connection.execute(query.limit(100001)).all()

    truncated = len(rows) > 100000
    rows = rows[:100000]
    grouped = {}
    meta = {}
    for row in rows:
        meta[row.system_id] = {
            "system_id": row.system_id, "sid": row.sid, "name": row.name,
            "landscape": row.landscape, "environment": row.environment,
        }
        ts = _aware(row.ts)
        epoch = int(ts.timestamp())
        bucket = epoch - (epoch % bucket_seconds)
        grouped.setdefault((row.system_id, bucket), []).append(float(row.value))

    output = []
    for system_key, info in sorted(meta.items(), key=lambda item: (item[1]["landscape"], item[1]["sid"])):
        points = []
        for (sid, bucket), values in sorted(grouped.items(), key=lambda item: item[0][1]):
            if sid != system_key:
                continue
            value = {
                "avg": sum(values) / len(values),
                "min": min(values),
                "max": max(values),
                "last": values[-1],
            }[aggregation]
            points.append({"ts": datetime.fromtimestamp(bucket, timezone.utc).isoformat(), "value": round(value, 4), "samples": len(values)})
        output.append({**info, "points": points})

    return {
        "metric": METRIC_MAP[metric], "window_hours": hours,
        "bucket_seconds": bucket_seconds, "aggregation": aggregation,
        "truncated": truncated, "series": output,
    }

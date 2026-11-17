"""Historical telemetry analytics for SAP Basis Sentinel.

The query path is intentionally portable: TimescaleDB accelerates the indexed
time-range scan, while aggregation remains SQL-dialect independent for SQLite
and vanilla PostgreSQL deployments.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

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
        func.count().label("samples"),
        func.count(func.distinct(metric_points.c.system_id)).label("systems_reporting"),
        func.count(func.distinct(metric_points.c.metric)).label("metrics_reporting"),
        func.max(metric_points.c.ts).label("last_sample_at"),
    ).select_from(metric_points.join(sap_systems, sap_systems.c.id == metric_points.c.system_id)).where(
        metric_points.c.ts >= since
    )
    systems_query = select(func.count()).select_from(sap_systems).where(sap_systems.c.enabled.is_(True))
    if landscape:
        query = query.where(sap_systems.c.landscape == landscape)
        systems_query = systems_query.where(sap_systems.c.landscape == landscape)
    with get_db() as connection:
        totals = connection.execute(query).one()
        total_systems = connection.execute(systems_query).scalar_one()
        metric_query = select(metric_points.c.metric).distinct().join(
            sap_systems, sap_systems.c.id == metric_points.c.system_id
        ).where(metric_points.c.ts >= since)
        if landscape:
            metric_query = metric_query.where(sap_systems.c.landscape == landscape)
        metric_keys = sorted(connection.execute(metric_query).scalars().all())
    return {
        "window_hours": hours,
        "systems_total": total_systems,
        "systems_reporting": totals.systems_reporting,
        "samples": totals.samples,
        "metrics_reporting": totals.metrics_reporting,
        "last_sample_at": _iso(totals.last_sample_at),
        "metrics": metric_keys,
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
        if connection.dialect.name == "postgresql":
            has_timescale = connection.execute(text(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb')"
            )).scalar()
            if has_timescale:
                bucket_expr = func.time_bucket(text(f"INTERVAL '{bucket_seconds} seconds'"), metric_points.c.ts)
            else:
                bucket_expr = func.to_timestamp(
                    func.floor(func.extract("epoch", metric_points.c.ts) / bucket_seconds) * bucket_seconds
                )
            joined = metric_points.join(sap_systems, sap_systems.c.id == metric_points.c.system_id)
            partition = [metric_points.c.system_id, bucket_expr]
            if aggregation == "last":
                ranked = select(
                    metric_points.c.system_id, sap_systems.c.sid, sap_systems.c.name,
                    sap_systems.c.landscape, sap_systems.c.environment,
                    bucket_expr.label("bucket"), metric_points.c.value.label("value"),
                    func.count().over(partition_by=partition).label("samples"),
                    func.row_number().over(partition_by=partition, order_by=metric_points.c.ts.desc()).label("rn"),
                ).select_from(joined).where(
                    metric_points.c.metric == metric, metric_points.c.ts >= since
                )
                if system_id is not None:
                    ranked = ranked.where(metric_points.c.system_id == system_id)
                if landscape:
                    ranked = ranked.where(sap_systems.c.landscape == landscape)
                subquery = ranked.subquery()
                query = select(subquery).where(subquery.c.rn == 1).order_by(
                    subquery.c.landscape, subquery.c.sid, subquery.c.bucket
                ).limit(100001)
            else:
                aggregate = {"avg": func.avg, "min": func.min, "max": func.max}[aggregation](metric_points.c.value)
                query = select(
                    metric_points.c.system_id, sap_systems.c.sid, sap_systems.c.name,
                    sap_systems.c.landscape, sap_systems.c.environment,
                    bucket_expr.label("bucket"), aggregate.label("value"),
                    func.count().label("samples"),
                ).select_from(joined).where(
                    metric_points.c.metric == metric, metric_points.c.ts >= since
                ).group_by(
                    metric_points.c.system_id, sap_systems.c.sid, sap_systems.c.name,
                    sap_systems.c.landscape, sap_systems.c.environment, bucket_expr
                )
                if system_id is not None:
                    query = query.where(metric_points.c.system_id == system_id)
                if landscape:
                    query = query.where(sap_systems.c.landscape == landscape)
                query = query.order_by(sap_systems.c.landscape, sap_systems.c.sid, bucket_expr).limit(100001)
            rows = connection.execute(query).all()
            truncated = len(rows) > 100000
            rows = rows[:100000]
            output_map = {}
            for row in rows:
                item = output_map.setdefault(row.system_id, {
                    "system_id": row.system_id, "sid": row.sid, "name": row.name,
                    "landscape": row.landscape, "environment": row.environment, "points": [],
                })
                item["points"].append({
                    "ts": _iso(row.bucket), "value": round(float(row.value), 4), "samples": int(row.samples),
                })
            output = list(output_map.values())
        else:
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

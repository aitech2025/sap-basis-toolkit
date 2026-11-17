"""Explainable operational intelligence built from observed Sentinel telemetry.

No ML model is trained or inferred here. Detection uses a robust median/MAD
baseline and capacity projections use a transparent least-squares trend.
"""
import json
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..db import capacity_snapshots, get_db, incidents, maintenance_windows, metric_points, sap_systems

MAX_ROWS = 200_000
FORECAST_METRICS = (
    ("disk", "Disk used", "disk_used_mb", "disk_total_mb"),
    ("memory", "Memory used", "memory_used_mb", "memory_total_mb"),
)


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value):
    return _aware(value).isoformat() if value else None


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _linear_forecast(points, horizon_days=30, threshold_pct=85.0):
    """Return a simple linear capacity projection; null when evidence is weak."""
    if len(points) < 3:
        return {"status": "insufficient_data", "points": len(points), "threshold_in_days": None, "reason": "At least three capacity snapshots are required."}
    origin = points[0][0]
    xs = [(_aware(ts) - origin).total_seconds() / 86400 for ts, _, _ in points]
    ys = [float(used) for _, used, _ in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 0:
        return {"status": "insufficient_data", "points": len(points), "threshold_in_days": None, "reason": "Snapshots do not span a measurable time interval."}
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    total_values = [float(total) for _, _, total in points if total is not None and float(total) > 0]
    total = _median(total_values) if total_values else None
    current = ys[-1]
    projection = max(0.0, intercept + slope * (xs[-1] + horizon_days))
    forecast = {
        "status": "projected" if slope > 0 and total else "stable_or_declining",
        "points": len(points),
        "current_used_mb": round(current, 2),
        "daily_change_mb": round(slope, 3),
        "horizon_days": horizon_days,
        "projected_used_mb": round(projection, 2),
        "total_mb": round(total, 2) if total else None,
        "projected_utilisation_pct": round(projection / total * 100, 2) if total else None,
        "threshold_pct": threshold_pct,
        "threshold_in_days": None,
    }
    if total and slope > 0:
        crossing_x = ((total * threshold_pct / 100) - intercept) / slope
        days = crossing_x - xs[-1]
        forecast["threshold_in_days"] = round(days, 1) if days >= 0 else 0.0
        forecast["status"] = "threshold_projected" if days <= horizon_days else "projected"
    return forecast


def capacity_forecasts(horizon_days: int = 30, threshold_pct: float = 85.0,
                       system_id: int | None = None, landscape: str | None = None) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=90)
    query = select(
        capacity_snapshots.c.system_id, sap_systems.c.sid, sap_systems.c.name,
        sap_systems.c.landscape, sap_systems.c.environment,
        capacity_snapshots.c.disk_used_mb, capacity_snapshots.c.disk_total_mb,
        capacity_snapshots.c.memory_used_mb, capacity_snapshots.c.memory_total_mb,
        capacity_snapshots.c.captured_at,
    ).join(sap_systems, sap_systems.c.id == capacity_snapshots.c.system_id).where(
        capacity_snapshots.c.captured_at >= since
    ).order_by(capacity_snapshots.c.system_id, capacity_snapshots.c.captured_at)
    if system_id is not None:
        query = query.where(capacity_snapshots.c.system_id == system_id)
    if landscape:
        query = query.where(sap_systems.c.landscape == landscape)
    with get_db() as connection:
        rows = connection.execute(query.limit(MAX_ROWS + 1)).all()
    grouped = {}
    for row in rows[:MAX_ROWS]:
        grouped.setdefault(row.system_id, {"system_id": row.system_id, "sid": row.sid,
            "name": row.name, "landscape": row.landscape, "environment": row.environment,
            "disk": [], "memory": []})
        item = grouped[row.system_id]
        item["disk"].append((row.captured_at, row.disk_used_mb, row.disk_total_mb))
        if row.memory_used_mb is not None and row.memory_total_mb is not None:
            item["memory"].append((row.captured_at, row.memory_used_mb, row.memory_total_mb))
    output = []
    for item in grouped.values():
        forecasts = {}
        for key, label, _, _ in FORECAST_METRICS:
            forecasts[key] = {"label": label, **_linear_forecast(item[key], horizon_days, threshold_pct)}
        output.append({k: v for k, v in item.items() if k not in ("disk", "memory")} | {"forecasts": forecasts})
    return {"lookback_days": 90, "horizon_days": horizon_days, "threshold_pct": threshold_pct,
            "truncated": len(rows) > MAX_ROWS, "systems": output}


def availability(hours: int = 24, system_id: int | None = None,
                 landscape: str | None = None) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = select(metric_points.c.system_id, sap_systems.c.sid, sap_systems.c.name,
        sap_systems.c.landscape, metric_points.c.value, metric_points.c.ts
    ).join(sap_systems, sap_systems.c.id == metric_points.c.system_id).where(
        metric_points.c.metric == "availability.system_up", metric_points.c.ts >= since
    ).order_by(metric_points.c.system_id, metric_points.c.ts)
    if system_id is not None:
        query = query.where(metric_points.c.system_id == system_id)
    if landscape:
        query = query.where(sap_systems.c.landscape == landscape)
    with get_db() as connection:
        rows = connection.execute(query.limit(MAX_ROWS + 1)).all()
        enabled_query = select(sap_systems.c.id, sap_systems.c.sid, sap_systems.c.name,
            sap_systems.c.landscape).where(sap_systems.c.enabled.is_(True))
        if system_id is not None:
            enabled_query = enabled_query.where(sap_systems.c.id == system_id)
        if landscape:
            enabled_query = enabled_query.where(sap_systems.c.landscape == landscape)
        registered = connection.execute(enabled_query).all()
        maintenance = connection.execute(select(maintenance_windows.c.scope_type,
            maintenance_windows.c.scope_value, maintenance_windows.c.starts_at,
            maintenance_windows.c.ends_at).where(
                maintenance_windows.c.starts_at < datetime.now(timezone.utc),
                maintenance_windows.c.ends_at > since)).all()
    observed = {}
    for row in rows[:MAX_ROWS]:
        item = observed.setdefault(row.system_id, {"system_id": row.system_id, "sid": row.sid,
            "name": row.name, "landscape": row.landscape, "samples": 0, "up_samples": 0,
            "down_samples": 0, "first_observed_at": row.ts, "last_observed_at": row.ts})
        item["samples"] += 1
        item["up_samples"] += 1 if float(row.value) >= 0.5 else 0
        item["down_samples"] += 1 if float(row.value) < 0.5 else 0
        item["last_observed_at"] = row.ts
    results = []
    for system in registered:
        item = observed.get(system.id)
        samples = item["samples"] if item else 0
        up = item["up_samples"] if item else 0
        down = item["down_samples"] if item else 0
        relevant = [w for w in maintenance if
            (w.scope_type == "global") or
            (w.scope_type == "landscape" and w.scope_value == system.landscape) or
            (w.scope_type == "system" and w.scope_value == str(system.id))]
        results.append({
            "system_id": system.id, "sid": system.sid, "name": system.name, "landscape": system.landscape,
            "samples": samples, "up_samples": up, "down_samples": down,
            "observed_availability_pct": round(up / samples * 100, 3) if samples else None,
            "first_observed_at": _iso(item["first_observed_at"]) if item else None,
            "last_observed_at": _iso(item["last_observed_at"]) if item else None,
            "maintenance_windows_overlapping_period": len(relevant),
            "measurement": "Observed sample ratio; not wall-clock SLA. Maintenance is reported separately and not subtracted.",
        })
    return {"window_hours": hours, "systems": results, "truncated": len(rows) > MAX_ROWS,
            "method": "up_samples / (up_samples + down_samples); unobserved time is excluded, not assumed available."}


def anomalies(hours: int = 24, system_id: int | None = None,
              landscape: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    recent_since = now - timedelta(hours=min(hours, 24))
    baseline_since = now - timedelta(days=7)
    query = select(metric_points.c.system_id, sap_systems.c.sid, sap_systems.c.name,
        sap_systems.c.landscape, metric_points.c.metric, metric_points.c.value, metric_points.c.ts
    ).join(sap_systems, sap_systems.c.id == metric_points.c.system_id).where(
        metric_points.c.ts >= baseline_since
    ).order_by(metric_points.c.system_id, metric_points.c.metric, metric_points.c.ts)
    if system_id is not None:
        query = query.where(metric_points.c.system_id == system_id)
    if landscape:
        query = query.where(sap_systems.c.landscape == landscape)
    with get_db() as connection:
        rows = connection.execute(query.limit(MAX_ROWS + 1)).all()
    grouped = {}
    for row in rows[:MAX_ROWS]:
        key = (row.system_id, row.metric)
        grouped.setdefault(key, {"system_id": row.system_id, "sid": row.sid, "name": row.name,
            "landscape": row.landscape, "metric": row.metric, "baseline": [], "recent": []})
        target = "recent" if _aware(row.ts) >= recent_since else "baseline"
        grouped[key][target].append(float(row.value))
        grouped[key]["latest_at"] = row.ts
    results = []
    for item in grouped.values():
        baseline, recent = item["baseline"], item["recent"]
        if len(baseline) < 12 or not recent:
            continue
        center = _median(baseline)
        mad = _median([abs(x - center) for x in baseline])
        latest = recent[-1]
        scale = 1.4826 * mad
        if scale > 0:
            score = (latest - center) / scale
        else:
            variance = sum((x - center) ** 2 for x in baseline) / len(baseline)
            std = math.sqrt(variance)
            score = (latest - center) / std if std > 0 else 0.0
        if abs(score) >= 3.5:
            metric_name = item["metric"]
            results.append({
                "system_id": item["system_id"], "sid": item["sid"], "name": item["name"],
                "landscape": item["landscape"], "metric": metric_name,
                "baseline_median": round(center, 4), "latest_value": round(latest, 4),
                "robust_z_score": round(score, 2), "direction": "high" if score > 0 else "low",
                "baseline_samples": len(baseline), "recent_samples": len(recent),
                "observed_at": _iso(item["latest_at"]),
                "severity": "critical" if abs(score) >= 6 else "warning",
                "method": "Median/MAD robust score; baseline is preceding 7 days excluding the recent window. Minimum 12 baseline samples.",
            })
    results.sort(key=lambda x: abs(x["robust_z_score"]), reverse=True)
    return {"baseline_days": 7, "recent_hours": min(hours, 24), "anomalies": results[:500],
            "truncated": len(rows) > MAX_ROWS}


def correlations(hours: int = 24, system_id: int | None = None,
                 landscape: str | None = None) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = select(incidents, sap_systems.c.sid, sap_systems.c.landscape).select_from(
        incidents.outerjoin(sap_systems, sap_systems.c.id == incidents.c.system_id)
    ).where(incidents.c.opened_at >= since).order_by(incidents.c.opened_at.desc())
    if system_id is not None:
        query = query.where(incidents.c.system_id == system_id)
    if landscape:
        query = query.where(sap_systems.c.landscape == landscape)
    with get_db() as connection:
        incident_rows = connection.execute(query.limit(500)).mappings().all()
        output = []
        for incident in incident_rows:
            start = _aware(incident["opened_at"]) - timedelta(minutes=30)
            end = _aware(incident["opened_at"]) + timedelta(minutes=30)
            metric_query = select(metric_points.c.metric, metric_points.c.value, metric_points.c.ts).where(
                metric_points.c.system_id == incident["system_id"],
                metric_points.c.ts >= start, metric_points.c.ts <= end,
            ).order_by(metric_points.c.ts)
            metric_rows = connection.execute(metric_query.limit(5000)).all() if incident["system_id"] else []
            grouped = {}
            for metric in metric_rows:
                grouped.setdefault(metric.metric, []).append((metric.ts, float(metric.value)))
            evidence = []
            for metric, values in grouped.items():
                before = [v for ts, v in values if _aware(ts) < _aware(incident["opened_at"])]
                after = [v for ts, v in values if _aware(ts) >= _aware(incident["opened_at"])]
                evidence.append({"metric": metric, "before_samples": len(before), "after_samples": len(after),
                    "before_avg": round(sum(before) / len(before), 3) if before else None,
                    "after_avg": round(sum(after) / len(after), 3) if after else None,
                    "change": round(sum(after) / len(after) - sum(before) / len(before), 3)
                    if before and after else None})
            evidence.sort(key=lambda x: abs(x["change"] or 0), reverse=True)
            output.append({
                "incident_id": incident["id"], "system_id": incident["system_id"],
                "sid": incident["sid"], "landscape": incident["landscape"],
                "title": incident["title"], "severity": incident["severity"],
                "status": incident["status"], "metric": incident["metric"],
                "opened_at": _iso(incident["opened_at"]), "last_seen_at": _iso(incident["last_seen_at"]),
                "source": incident["source"], "evidence_window_minutes": 30,
                "related_metrics": evidence[:10],
                "interpretation": "Temporal association only; changes are not proof of causation.",
            })
    return {"window_hours": hours, "incidents": output}

"""Evidence-based, read-only root-cause hypotheses for SAP incidents.

This engine ranks contributing domains from observed metric changes around an incident.
It does not assert causation, infer missing telemetry, or execute remediation.
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..db import incidents, metric_points, monitor_snapshots, sap_systems, get_db

WINDOW_MINUTES = 30
MAX_METRIC_ROWS = 20_000
# Metric thresholds are investigation triggers, not universal SAP sizing limits.
SIGNALS = {
    "hana.cpu_pct": ("HANA resource pressure", "HANA", 80, "high"),
    "hana.memory_used_pct": ("HANA resource pressure", "HANA", 85, "high"),
    "hana.disk_used_pct": ("HANA storage pressure", "HANA", 85, "high"),
    "hana.alerts_high": ("HANA resource or service alert", "HANA", 1, "high"),
    "hana.replication_ok": ("HANA replication health", "HANA", 1, "low"),
    "wp.dia_busy_pct": ("Dialog work-process saturation", "ABAP", 80, "high"),
    "wp.btc_busy_pct": ("Background work-process saturation", "ABAP", 90, "high"),
    "wp.long_running": ("Long-running ABAP work processes", "ABAP", 1, "high"),
    "wp.stopped": ("Stopped ABAP work processes", "ABAP", 1, "high"),
    "dumps.last_hour": ("ABAP runtime errors", "ABAP", 20, "high"),
    "syslog.errors_last_hour": ("ABAP system-log errors", "ABAP", 10, "high"),
    "updates.errors": ("Failed update processing", "ABAP", 1, "high"),
    "trfc.errors": ("Transactional RFC failures", "Interfaces", 1, "high"),
    "qrfc.blocked_queues": ("Blocked qRFC queues", "Interfaces", 1, "high"),
    "idoc.errors_24h": ("IDoc processing failures", "Interfaces", 10, "high"),
    "availability.instances_down": ("SAP instance availability", "Availability", 1, "high"),
    "availability.system_up": ("SAP system availability", "Availability", 1, "low"),
    "collector.failed": ("Collector connectivity failure", "Connectivity", 1, "high"),
    "host.memory_used_pct": ("Host memory pressure", "Host", 85, "high"),
    "host.disk_used_pct": ("Host root-filesystem pressure", "Host", 85, "high"),
}
RECOMMENDATIONS = {
    "HANA": [
        "Check HANA expensive statements, active statements and thread samples around the incident time.",
        "Review M_SERVICE_THREADS, M_LOAD_HISTORY_HOST, memory allocation and current HANA alerts.",
        "Check data/log volume headroom and HANA service health before changing workload or allocation.",
    ],
    "ABAP": [
        "Inspect SM50/SM66 for long-running work processes, program, action and wait reason.",
        "Review ST22 dumps and SM21 system log entries in the same time window.",
        "Check SM13 update failures and correlate affected transactions, users and application servers.",
    ],
    "Interfaces": [
        "Inspect SM58 tRFC and SMQ1/SMQ2 qRFC queues, destinations and error text.",
        "Review WE02/WE05 IDoc status records and partner/port errors.",
        "Check destination reachability and middleware availability before reprocessing units.",
    ],
    "Availability": [
        "Check sapcontrol GetProcessList and GetSystemInstanceList for affected instances.",
        "Review dispatcher, message server and enqueue service health plus recent restarts.",
        "Correlate host/service logs and any planned maintenance before restarting components.",
    ],
    "Connectivity": [
        "Validate collector reachability, TLS, DNS and network path from the customer-side connector.",
        "Check SAP start service and HANA endpoint availability independently of the monitoring agent.",
        "Treat missing telemetry as unknown health until a successful collection confirms system state.",
    ],
    "Host": [
        "Check host memory pressure, swap activity and top resident processes around the incident window.",
        "Check root filesystem capacity, inode utilisation and recent log or trace growth.",
        "Correlate host evidence with the SAP instance on that host; confirm the collector is attached to the correct SAP system.",
    ],
}


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value):
    return _aware(value).isoformat() if value else None


def analyze(incident_id: int) -> dict:
    with get_db() as connection:
        row = connection.execute(
            select(incidents, sap_systems.c.sid, sap_systems.c.name, sap_systems.c.landscape)
            .select_from(incidents.outerjoin(sap_systems, sap_systems.c.id == incidents.c.system_id))
            .where(incidents.c.id == incident_id)
        ).mappings().first()
        if row is None:
            raise LookupError("Incident not found")
        incident = dict(row)
        opened = _aware(incident["opened_at"])
        start, end = opened - timedelta(minutes=WINDOW_MINUTES), opened + timedelta(minutes=WINDOW_MINUTES)
        if incident["system_id"] is None:
            return {
                "incident": {"id": incident_id, "title": incident["title"], "sid": None, "opened_at": _iso(opened)},
                "analysis_window": {"starts_at": _iso(start), "ends_at": _iso(end), "minutes_each_side": WINDOW_MINUTES},
                "hypotheses": [], "evidence": [], "coverage": {"system_metrics": False, "metrics_observed": 0},
                "limitations": ["Incident is not associated with an SAP system; system telemetry cannot be correlated."],
                "interpretation": "No root-cause hypothesis can be ranked without a system association.",
            }
        metrics = connection.execute(
            select(metric_points.c.metric, metric_points.c.value, metric_points.c.ts)
            .where(metric_points.c.system_id == incident["system_id"],
                   metric_points.c.ts >= start, metric_points.c.ts <= end)
            .order_by(metric_points.c.ts).limit(MAX_METRIC_ROWS + 1)
        ).all()
        snapshots = connection.execute(
            select(monitor_snapshots.c.monitor, monitor_snapshots.c.source, monitor_snapshots.c.health,
                   monitor_snapshots.c.captured_at, monitor_snapshots.c.data)
            .where(monitor_snapshots.c.system_id == incident["system_id"],
                   monitor_snapshots.c.captured_at >= start, monitor_snapshots.c.captured_at <= end)
            .order_by(monitor_snapshots.c.captured_at.desc()).limit(500)
        ).all()

    grouped = {}
    for metric in metrics[:MAX_METRIC_ROWS]:
        grouped.setdefault(metric.metric, []).append((_aware(metric.ts), float(metric.value)))
    evidence, by_domain = [], {}
    for metric, (label, domain, threshold, direction) in SIGNALS.items():
        values = grouped.get(metric, [])
        before = [v for ts, v in values if ts < opened]
        after = [v for ts, v in values if ts >= opened]
        if not values:
            continue
        latest_ts, latest = values[-1]
        baseline = sum(before) / len(before) if before else None
        recent = sum(after) / len(after) if after else latest
        triggered = latest >= threshold if direction == "high" else latest < threshold
        delta = recent - baseline if baseline is not None and after else None
        worsening = delta is not None and ((delta > 0 and direction == "high") or (delta < 0 and direction == "low"))
        primary = metric == incident.get("metric")
        strength = 0
        if triggered:
            strength += 2
        if worsening:
            strength += 1
        if latest_ts >= opened - timedelta(minutes=10):
            strength += 1
        if primary:
            strength = max(0, strength - 2)  # a trigger is not independent corroboration
        item = {
            "metric": metric, "label": label, "domain": domain, "latest_value": round(latest, 3),
            "threshold": threshold, "direction": direction, "triggered": triggered,
            "before_avg": round(baseline, 3) if baseline is not None else None,
            "after_avg": round(recent, 3) if after else None,
            "change": round(delta, 3) if delta is not None else None,
            "before_samples": len(before), "after_samples": len(after),
            "observed_at": _iso(latest_ts), "primary_incident_metric": primary,
            "corroboration_points": strength,
        }
        evidence.append(item)
        if strength:
            bucket = by_domain.setdefault(domain, {"score": 0, "signals": []})
            bucket["score"] += strength
            bucket["signals"].append(item)

    # Add concise structured snapshot evidence without exposing users, credentials or raw payloads.
    snapshot_evidence = []
    for snap in snapshots:
        try:
            data = json.loads(snap.data or "{}")
        except (TypeError, ValueError):
            data = {}
        summary = data.get("summary", {}) if isinstance(data, dict) else {}
        snapshot_evidence.append({
            "monitor": snap.monitor, "source": snap.source, "health": snap.health,
            "captured_at": _iso(snap.captured_at),
            "summary": {k: v for k, v in summary.items() if isinstance(v, (str, int, float, bool, type(None)))},
        })

    hypotheses = []
    for domain, item in by_domain.items():
        signals = item["signals"]
        # Cap score so a domain with many sampled metrics does not dominate solely by volume.
        score = min(10, item["score"])
        confidence = "high" if score >= 6 and len(signals) >= 2 else "medium" if score >= 3 else "low"
        hypotheses.append({
            "domain": domain, "title": {
                "HANA": "HANA database or resource pressure",
                "ABAP": "ABAP application-server pressure or runtime errors",
                "Interfaces": "RFC, qRFC or IDoc processing disruption",
                "Availability": "SAP instance or system availability degradation",
                "Connectivity": "Monitoring or endpoint connectivity disruption",
                "Host": "Operating-system memory or filesystem pressure",
            }[domain],
            "score": score, "confidence": confidence,
            "supporting_signals": [{"metric": s["metric"], "label": s["label"],
                "latest_value": s["latest_value"], "threshold": s["threshold"],
                "change": s["change"], "observed_at": s["observed_at"]} for s in signals],
            "recommended_checks": RECOMMENDATIONS[domain],
            "reason": f"{len(signals)} observed signal(s) in the incident correlation window; score is a deterministic evidence ranking, not a probability.",
        })
    hypotheses.sort(key=lambda h: (h["score"], len(h["supporting_signals"])), reverse=True)

    present_domains = {h["domain"] for h in hypotheses}
    monitored = {s.monitor for s in snapshots}
    coverage = {
        "system_metrics": True, "metrics_observed": len(grouped),
        "expected_domains": ["HANA", "ABAP", "Interfaces", "Availability", "Connectivity", "Host"],
        "domains_with_supporting_signals": sorted(present_domains),
        "monitor_snapshots_in_window": len(snapshot_evidence),
        "monitors_observed": sorted(monitored),
        "metric_query_truncated": len(metrics) > MAX_METRIC_ROWS,
    }
    limitations = []
    if not metrics:
        limitations.append("No metric points were recorded in the incident window.")
    if not snapshots:
        limitations.append("No monitor snapshots were recorded in the incident window.")
    if len(metrics) > MAX_METRIC_ROWS:
        limitations.append("Metric evidence was capped; rerun with a narrower window after investigation.")
    limitations.append("Host memory and root-filesystem evidence is available only when a node_exporter collector is configured; CPU percentage, network throughput and disk I/O rates are not yet collected.")
    limitations.append("Only evidence from the incident's registered SAP system is correlated; shared infrastructure dependencies are not inferred.")
    return {
        "incident": {"id": incident_id, "title": incident["title"], "severity": incident["severity"],
            "status": incident["status"], "sid": incident["sid"], "system_name": incident["name"],
            "landscape": incident["landscape"], "metric": incident["metric"], "value": incident["value"],
            "opened_at": _iso(opened)},
        "analysis_window": {"starts_at": _iso(start), "ends_at": _iso(end), "minutes_each_side": WINDOW_MINUTES},
        "hypotheses": hypotheses, "evidence": evidence, "snapshot_evidence": snapshot_evidence,
        "coverage": coverage, "limitations": limitations,
        "interpretation": "Ranked hypotheses are investigation leads, not causal proof. Confidence describes evidence coverage and strength, not statistical probability. No telemetry is imputed.",
    }

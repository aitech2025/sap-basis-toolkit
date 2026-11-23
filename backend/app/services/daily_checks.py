"""Basis daily checks: one matrix of the classic morning checklist for every system.

Each check reads the latest value of a metric (metric_latest) and classifies it with the same thresholds as alerting
(the system's effective rule). Checks without an alert rule use the defaults below. Values older than the check's
freshness window are shown as stale rather than green, so a silent collector never looks healthy.
"""
import csv
import io
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..db import alert_rules, daily_check_reviews, get_db
from . import monitors, systems

# key, area, label, transaction, default (operator, warning, critical) when no rule exists, freshness minutes
CHECKS = [
    ("availability.system_up", "Availability", "System available", "SM51 / SMMS", ("<", None, 1), 30),
    ("availability.instances_down", "Availability", "Instances down", "SM51", (">=", 1, None), 30),
    ("hana.system_up", "Availability", "HANA instance up", "HDB info", ("<", None, 1), 30),
    ("db.os_process_up", "Availability", "Database process running", "ST06 / DB02", ("<", None, 1), 60),
    ("agent.up", "Availability", "ABAP agent reporting", "SM37 Z_SENTINEL_PUSH", ("<", 1, None), 60),
    ("wp.dia_busy_pct", "Work processes", "Dialog work processes busy", "SM50 / SM66", (">=", 80, 95), 30),
    ("wp.long_running", "Work processes", "Long-running work processes", "SM66", (">=", 1, None), 30),
    ("abap.queue_dia", "Work processes", "Dialog queue", "SM50 / dpmon", (">=", 10, 50), 30),
    ("abap.dialog_response_ms", "Performance", "Dialog response time", "ST03", (">=", 1500, 3000), 30),
    ("abap.buffer_swaps", "Performance", "Buffer swaps", "ST02", (">=", 1000, 10000), 60),
    ("dumps.last_24h", "Errors", "Short dumps (24 h)", "ST22", (">=", 50, 200), 60),
    ("syslog.errors_last_hour", "Errors", "System log errors (1 h)", "SM21", (">=", 10, 50), 30),
    ("updates.errors", "Errors", "Update errors", "SM13", (">=", 1, None), 60),
    ("applog.error_logs_24h", "Errors", "Application log errors (24 h)", "SLG1", (">=", 100, None), 120),
    ("jobs.cancelled_24h", "Background jobs", "Cancelled jobs (24 h)", "SM37", (">=", 1, 10), 120),
    ("abap.locks_oldest_min", "Locks", "Oldest lock (minutes)", "SM12", (">=", 60, 240), 30),
    ("abap.enq_table_used_pct", "Locks", "Lock table used", "SM12 statistics", (">=", 70, 90), 30),
    ("trfc.errors", "Interfaces", "tRFC errors", "SM58", (">=", 1, 50), 60),
    ("qrfc.blocked_queues", "Interfaces", "Blocked qRFC queues", "SMQ1 / SMQ2", (">=", 1, 5), 60),
    ("idoc.errors_24h", "Interfaces", "IDoc errors (24 h)", "WE02 / BD87", (">=", 1, 50), 120),
    ("batchinput.error_sessions_7d", "Interfaces", "Batch input errors (7 days)", "SM35", (">=", 1, None), 240),
    ("numrange.max_used_pct", "Capacity", "Number range used", "SNRO", (">=", 80, 90), 1440),
    ("hana.memory_used_pct", "Database", "HANA memory", "HANA cockpit", (">=", 85, 95), 30),
    ("hana.disk_used_pct", "Database", "HANA data volume", "DB02", (">=", 85, 95), 60),
    ("hana.log_disk_used_pct", "Database", "HANA log volume", "DB02", (">=", 80, 90), 60),
    ("hana.data_backup_age_h", "Database", "Last data backup (hours)", "DB12 / backup catalog", (">=", 26, 50), 120),
    ("hana.log_backup_age_h", "Database", "Last log backup (hours)", "DB12", (">=", 1, 4), 120),
    ("hana.replication_ok", "Database", "System replication", "HSR", ("<", None, 1), 60),
    ("hana.alerts_high", "Database", "HANA high alerts", "HANA cockpit", (">=", 1, 5), 60),
    ("host.cpu_pct", "Operating system", "App host CPU", "ST06", (">=", 85, 95), 60),
    ("host.memory_used_pct", "Operating system", "App host memory", "ST06", (">=", 90, 97), 60),
    ("host.swap_used_pct", "Operating system", "App host swap", "ST06", (">=", 20, 50), 60),
    ("host.disk_used_pct", "Operating system", "App host file systems", "ST06 / df", (">=", 85, 95), 60),
    ("db.host_cpu_pct", "Operating system", "DB host CPU", "ST06", (">=", 85, 95), 60),
    ("db.host_disk_used_pct", "Operating system", "DB host file systems", "ST06 / df", (">=", 85, 95), 60),
    ("security.prod_clients_open", "Security", "Production client open", "SCC4", (">=", 1, None), 1440),
    ("security.sapstar_risk", "Security", "SAP* missing or unlocked", "SU01", (">=", 1, None), 1440),
    ("certificates.min_days", "Security", "Certificate expiry (days)", "STRUST / SMICM", ("<", 30, 7), 1440),
]
FLAGS = {"availability.system_up", "hana.system_up", "db.os_process_up", "agent.up", "hana.replication_ok"}
# Checks that do not come from a monitoring layer's collectors (certificates: TLS endpoints and STRUST imports).
UNLAYERED = {"certificates.min_days"}


def _format(key: str) -> str:
    if key in FLAGS:
        return "flag"
    if key.endswith("_pct"):
        return "pct"
    if key.endswith("_ms"):
        return "ms"
    if key.endswith("_age_h"):
        return "hours"
    if key.endswith("_min"):
        return "minutes"
    if key.endswith("_days"):
        return "days"
    return "count"


RANK = {"critical": 4, "warning": 3, "stale": 2, "ok": 1, "no_data": 0}


def _classify(op: str, warning, critical, value: float) -> str:
    hit = (lambda t: t is not None and (value >= t if op == ">=" else value <= t if op == "<=" else value > t if op == ">" else value < t))
    if hit(critical):
        return "critical"
    if hit(warning):
        return "warning"
    return "ok"


def _rules_by_system(registry: list[dict]) -> dict[int, dict[str, dict]]:
    with get_db() as connection:
        rows = [dict(r) for r in connection.execute(select(alert_rules).where(alert_rules.c.enabled.is_(True))).mappings()]
    rank = {"global": 0, "landscape": 1, "system": 2}
    out = {}
    for system in registry:
        chosen: dict[str, dict] = {}
        for r in rows:
            applies = (r["scope_type"] == "global" or (r["scope_type"] == "landscape" and r["scope_value"] == system["landscape"])
                       or (r["scope_type"] == "system" and r["scope_value"] == str(system["id"])))
            if applies and (r["metric"] not in chosen or rank[r["scope_type"]] > rank[chosen[r["metric"]]["scope_type"]]):
                chosen[r["metric"]] = r
        out[system["id"]] = chosen
    return out


def _certificate_days() -> dict[int, float]:
    from ..api.toolkit import certificate_summary  # endpoints (live TLS) and imported STRUST certificates
    summary = certificate_summary()
    days: dict[int, float] = {}
    for c in summary["endpoints"] + summary["strust_certificates"]:
        if c.get("system_id") and c.get("days_remaining") is not None:
            days[c["system_id"]] = min(days.get(c["system_id"], 10**6), float(c["days_remaining"]))
    return days


def matrix(landscape: str | None = None, environment: str | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    registry = [s for s in systems.list_systems(enabled=True, landscape=landscape)
                if not environment or s["environment"] == environment]
    latest = monitors.latest_metrics([s["id"] for s in registry]) if registry else {}
    rules = _rules_by_system(registry)
    cert_days = _certificate_days()
    rows, totals = [], {k: 0 for k in RANK}
    for system in registry:
        values = dict(latest.get(system["id"], {}))
        if system["id"] in cert_days:
            values["certificates.min_days"] = (cert_days[system["id"]], now)
        cells, counts = {}, {k: 0 for k in RANK}
        layers = systems.layers_of(system)
        for key, _area, _label, _tcode, default, fresh_min in CHECKS:
            if key not in values:
                continue  # not applicable / not collected for this system: shown empty, not as a failure
            if key not in UNLAYERED and systems.metric_layer(key) not in layers:
                continue  # layer turned off: its last values would only age into "stale", which reads as a fault
            value, ts = values[key]
            rule = rules[system["id"]].get(key)
            op, warning, critical = (rule["operator"], rule["warning"], rule["critical"]) if rule else default
            if now - ts > timedelta(minutes=fresh_min):
                status = "stale"
            else:
                status = _classify(op, warning, critical, float(value))
            cells[key] = {"status": status, "value": round(float(value), 2), "at": ts.isoformat(),
                          "threshold": {"operator": op, "warning": warning, "critical": critical,
                                        "source": "rule" if rule else "default"}}
            counts[status] += 1
        overall = max((c["status"] for c in cells.values()), key=lambda s: RANK[s], default="no_data")
        for k, v in counts.items():
            totals[k] += v
        rows.append({"id": system["id"], "sid": system["sid"], "name": system["name"], "landscape": system["landscape"],
                     "environment": system["environment"], "overall": overall, "counts": counts, "cells": cells})
    order = {"critical": 0, "warning": 1, "stale": 2, "ok": 3, "no_data": 4}
    rows.sort(key=lambda r: (order[r["overall"]], r["landscape"] or "", r["sid"]))
    used = {k for r in rows for k in r["cells"]}
    checks = [{"key": k, "area": a, "label": label, "tcode": t, "fresh_minutes": f, "format": _format(k)}
              for k, a, label, t, _d, f in CHECKS if k in used]
    return {"generated_at": now.isoformat(), "landscape": landscape, "environment": environment,
            "checks": checks, "systems": rows, "totals": totals,
            "summary": {"systems": len(rows), **{s: sum(1 for r in rows if r["overall"] == s) for s in RANK}}}


def _display(fmt: str, value: float) -> str:
    if fmt == "flag":
        return "up" if value >= 1 else "down"
    suffix = {"pct": "%", "ms": " ms", "hours": " h", "minutes": " min", "days": " days"}.get(fmt, "")
    return f"{value:g}{suffix}"


def to_csv(data: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["SID", "Landscape", "Environment", "Overall"] + [f"{c['label']} ({c['tcode']})" for c in data["checks"]])
    for r in data["systems"]:
        w.writerow([r["sid"], r["landscape"], r["environment"], r["overall"]] +
                   [(f"{r['cells'][c['key']]['status']}: {_display(c['format'], r['cells'][c['key']]['value'])}" if c["key"] in r["cells"] else "")
                    for c in data["checks"]])
    return out.getvalue()


def review(actor: str, landscape: str | None, comment: str | None, today: str | None = None) -> dict:
    data = matrix(landscape)
    now = datetime.now(timezone.utc)
    check_date = today or now.date().isoformat()
    with get_db() as connection:
        row = connection.execute(daily_check_reviews.insert().values(
            check_date=check_date, landscape=landscape, reviewed_by=actor[:80], reviewed_at=now,
            comment=(comment or "").strip()[:2000] or None, summary=json.dumps(data["summary"]),
            results=json.dumps(data, default=str)).returning(daily_check_reviews.c.id)).scalar_one()
    return {"id": row, "check_date": check_date, "reviewed_by": actor, "reviewed_at": now.isoformat(), "summary": data["summary"]}


def reviews(days: int = 30) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    with get_db() as connection:
        rows = connection.execute(select(daily_check_reviews.c.id, daily_check_reviews.c.check_date, daily_check_reviews.c.landscape,
                                         daily_check_reviews.c.reviewed_by, daily_check_reviews.c.reviewed_at,
                                         daily_check_reviews.c.comment, daily_check_reviews.c.summary)
                                  .where(daily_check_reviews.c.check_date >= since)
                                  .order_by(daily_check_reviews.c.reviewed_at.desc())).mappings().all()
    return [{**dict(r), "reviewed_at": r["reviewed_at"].isoformat() if hasattr(r["reviewed_at"], "isoformat") else r["reviewed_at"],
             "summary": json.loads(r["summary"])} for r in rows]


def get_review(review_id: int) -> dict:
    with get_db() as connection:
        r = connection.execute(select(daily_check_reviews).where(daily_check_reviews.c.id == review_id)).mappings().first()
    if r is None:
        raise LookupError("Review not found")
    return {**{k: r[k] for k in ("id", "check_date", "landscape", "reviewed_by", "comment")},
            "reviewed_at": r["reviewed_at"].isoformat() if hasattr(r["reviewed_at"], "isoformat") else r["reviewed_at"],
            "results": json.loads(r["results"])}

"""Alert rules, incident lifecycle and maintenance windows.

* Collectors produce metric samples per system. ``evaluate(system, metrics)`` applies the most
  specific enabled rule per metric (system > landscape > global) and opens, escalates, updates or
  auto-resolves one incident per (rule metric, system).
* Module alerts (certificates, jobs, locks, transports, growth) open incidents through
  ``raise_module_incident``; they are resolved by people.
* Maintenance windows suppress notifications (incidents are still recorded, flagged
  ``in_maintenance``) for a system, a landscape or everything.
"""
import json
import operator as op
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, update

from ..db import alert_rules, get_db, incident_events, incidents, maintenance_windows, sap_systems
from . import notifications

OPS = {">": op.gt, ">=": op.ge, "<": op.lt, "<=": op.le}
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}

# metric, name, operator, warning, critical, description
BUILTIN_RULES = [
    ("availability.system_up", "System unavailable", "<", None, 1,
     "No running dispatcher/message server instance reported by sapcontrol"),
    ("availability.instances_down", "Instances not running", ">=", 1, None, "One or more instances are not GREEN"),
    ("hana.system_up", "HANA database down", "<", None, 1, "The HANA instance is not GREEN in its own sapstartsrv"),
    ("hana.services_inactive", "HANA service not active", ">=", None, 1, "A HANA service (M_SERVICES) is not ACTIVE"),
    ("abap.queue_dia", "Dialog requests queued in the dispatcher", ">=", 10, 50, "Requests waiting for a free dialog work process (GetQueueStatistic)"),
    ("abap.queue_upd", "Update requests queued", ">=", 20, 100, "Update requests waiting in the dispatcher queue"),
    ("abap.enq_table_used_pct", "Enqueue lock table filling up", ">=", 70, 90, "Lock table usage (EnqGetStatistic); an overflow stops all locking"),
    ("abap.dialog_response_ms", "Dialog response time", ">=", 1500, 3000, "Average dialog response time (CCMS, instance shared memory)"),
    ("ccms.red_now", "CCMS reports a red value", ">=", 1, None, "An instance CCMS monitoring attribute is currently red"),
    ("agent.up", "ABAP push agent silent", "<", 1, None, "The ABAP push agent (job Z_SENTINEL_PUSH) has not sent data within AGENT_SILENT_SECONDS"),
    ("collector.failed", "Collector cannot reach the system", ">=", 1, None,
     "A live collector failed; data is stale until it recovers"),
    ("wp.dia_busy_pct", "Dialog work processes busy", ">=", 80, 95, "Share of DIA work processes running"),
    ("wp.btc_busy_pct", "Background work processes busy", ">=", 90, None, "Share of BTC work processes running"),
    ("wp.long_running", "Long-running work processes", ">=", 1, 5,
     "Work processes busy for longer than WP_LONG_RUNNING_SECONDS"),
    ("wp.stopped", "Work processes stopped or ended", ">=", 1, 3, "Work processes in status Stopped/Ended"),
    ("dumps.last_hour", "Short dumps (last hour)", ">=", 20, 100, "ABAP runtime errors in ST22"),
    ("syslog.errors_last_hour", "System log errors (last hour)", ">=", 10, 50, "SM21 entries with error severity"),
    ("updates.errors", "Update errors", ">=", 1, 10, "Failed update requests (SM13)"),
    ("trfc.errors", "tRFC errors", ">=", 1, 50, "tRFC LUWs in error status (SM58)"),
    ("qrfc.blocked_queues", "Blocked qRFC queues", ">=", 1, 10, "Queues in SYSFAIL/CPICERR/STOP (SMQ1/SMQ2)"),
    ("idoc.errors_24h", "IDoc errors (24 h)", ">=", 10, 100, "IDocs in error status created in the last 24 hours"),
    ("hana.memory_used_pct", "HANA memory used", ">=", 85, 95, "Used memory vs. allocation limit"),
    # OS layer (application host via os_ssh or node_exporter; database host via os_ssh)
    ("host.memory_used_pct", "Application host memory used", ">=", 90, 97, "Memory in use on the application server (excluding cache)"),
    ("host.disk_used_pct", "Application host file system full", ">=", 85, 95, "Fullest of /, /usr/sap, /sapmnt on the application server"),
    ("db.os_process_up", "Database process not running", "<", None, 1, "The database main process (hdbindexserver, ora_pmon, db2sysc ...) is missing on the database host"),
    ("db.host_memory_used_pct", "Database host memory used", ">=", 92, 98, "Memory in use on the database server"),
    ("db.host_disk_used_pct", "Database host file system full", ">=", 85, 95, "Fullest database file system (/hana/*, /oracle, /db2 ...)"),
    ("hana.cpu_pct", "HANA host CPU", ">=", 80, 95, "Host CPU utilisation"),
    ("hana.disk_used_pct", "HANA data volume used", ">=", 85, 95, "Data volume usage"),
    ("hana.alerts_high", "HANA high-priority alerts", ">=", 1, 5, "Current statistics server alerts rated high/error"),
    ("hana.data_backup_age_h", "Last successful data backup", ">=", 26, 50, "Hours since the last complete data backup"),
    ("hana.log_backup_age_h", "Last successful log backup", ">=", 1, 4, "Hours since the last log backup"),
    ("hana.replication_ok", "HANA system replication not active", "<", None, 1,
     "At least one replication channel is not ACTIVE"),
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


# ------------------------------------------------------------------------------- rules
def seed_builtin_rules() -> None:
    with get_db() as connection:
        existing = set(connection.execute(select(alert_rules.c.metric).where(
            alert_rules.c.builtin.is_(True), alert_rules.c.scope_type == "global")).scalars().all())
        for metric, name, oper, warning, critical, description in BUILTIN_RULES:
            if metric not in existing:
                connection.execute(alert_rules.insert().values(
                    name=name, metric=metric, operator=oper, warning=warning, critical=critical,
                    scope_type="global", enabled=True, description=description, builtin=True))


def list_rules() -> list[dict]:
    with get_db() as connection:
        rows = connection.execute(select(alert_rules).order_by(alert_rules.c.metric, alert_rules.c.scope_type)).mappings().all()
    return [_rule_view(r) for r in rows]


def _rule_view(r) -> dict:
    item = dict(r)
    item["enabled"], item["builtin"] = bool(item["enabled"]), bool(item["builtin"])
    item["updated_at"] = _iso(item["updated_at"])
    return item


def save_rule(data: dict, rule_id: int | None = None) -> dict:
    if data.get("operator") and data["operator"] not in OPS:
        raise ValueError(f"operator must be one of {', '.join(OPS)}")
    if data.get("scope_type", "global") not in ("global", "landscape", "system"):
        raise ValueError("scope_type must be global, landscape or system")
    if data.get("scope_type") in ("landscape", "system") and not data.get("scope_value"):
        raise ValueError("scope_value is required for landscape/system rules")
    values = {**data, "updated_at": _now()}
    with get_db() as connection:
        if rule_id is None:
            if values.get("warning") is None and values.get("critical") is None:
                raise ValueError("Set a warning and/or critical threshold")
            row = connection.execute(alert_rules.insert().values(builtin=False, **values).returning(alert_rules)).mappings().one()
        else:
            if connection.execute(select(alert_rules.c.id).where(alert_rules.c.id == rule_id)).first() is None:
                raise LookupError("Rule not found")
            connection.execute(update(alert_rules).where(alert_rules.c.id == rule_id).values(**values))
            row = connection.execute(select(alert_rules).where(alert_rules.c.id == rule_id)).mappings().one()
    return _rule_view(row)


def delete_rule(rule_id: int) -> None:
    with get_db() as connection:
        row = connection.execute(select(alert_rules.c.builtin).where(alert_rules.c.id == rule_id)).first()
        if row is None:
            raise LookupError("Rule not found")
        if row.builtin:
            raise ValueError("Built-in rules cannot be deleted; disable them or override per landscape/system")
        connection.execute(alert_rules.delete().where(alert_rules.c.id == rule_id))


def effective_rules(system: dict) -> dict[str, dict]:
    """Most specific enabled rule per metric for this system."""
    rank = {"global": 0, "landscape": 1, "system": 2}
    chosen: dict[str, dict] = {}
    with get_db() as connection:
        rows = connection.execute(select(alert_rules).where(or_(
            alert_rules.c.scope_type == "global",
            and_(alert_rules.c.scope_type == "landscape", alert_rules.c.scope_value == system["landscape"]),
            and_(alert_rules.c.scope_type == "system", alert_rules.c.scope_value == str(system["id"])),
        ))).mappings().all()
    for r in rows:
        current = chosen.get(r["metric"])
        if current is None or rank[r["scope_type"]] > rank[current["scope_type"]]:
            chosen[r["metric"]] = dict(r)
    return {m: r for m, r in chosen.items() if r["enabled"]}


def classify(rule: dict, value: float) -> str | None:
    compare = OPS[rule["operator"]]
    if rule["critical"] is not None and compare(value, rule["critical"]):
        return "critical"
    if rule["warning"] is not None and compare(value, rule["warning"]):
        return "warning"
    return None


# ------------------------------------------------------------------------------- maintenance
def active_maintenance(system: dict | None, at: datetime | None = None) -> dict | None:
    at = at or _now()
    with get_db() as connection:
        query = select(maintenance_windows).where(maintenance_windows.c.starts_at <= at, maintenance_windows.c.ends_at > at)
        rows = connection.execute(query).mappings().all()
    for w in rows:
        if w["scope_type"] == "global":
            return dict(w)
        if system and w["scope_type"] == "landscape" and w["scope_value"] == system.get("landscape"):
            return dict(w)
        if system and w["scope_type"] == "system" and w["scope_value"] == str(system.get("id")):
            return dict(w)
    return None


def list_maintenance(include_past: bool = False) -> list[dict]:
    query = select(maintenance_windows).order_by(maintenance_windows.c.starts_at.desc())
    if not include_past:
        query = query.where(maintenance_windows.c.ends_at > _now())
    with get_db() as connection:
        rows = connection.execute(query).mappings().all()
    now = _now()
    result = []
    for r in rows:
        item = dict(r)
        starts, ends = datetime.fromisoformat(_iso(item["starts_at"])), datetime.fromisoformat(_iso(item["ends_at"]))
        item["state"] = "active" if starts <= now < ends else "planned" if starts > now else "past"
        for k in ("starts_at", "ends_at", "created_at"):
            item[k] = _iso(item[k])
        result.append(item)
    return result


def create_maintenance(data: dict, actor: str) -> dict:
    if data["ends_at"] <= data["starts_at"]:
        raise ValueError("ends_at must be after starts_at")
    if data["scope_type"] not in ("global", "landscape", "system"):
        raise ValueError("scope_type must be global, landscape or system")
    if data["scope_type"] != "global" and not data.get("scope_value"):
        raise ValueError("scope_value is required for landscape/system windows")
    with get_db() as connection:
        row = connection.execute(maintenance_windows.insert().values(created_by=actor, **data)
                                 .returning(maintenance_windows)).mappings().one()
    return {**dict(row), **{k: _iso(row[k]) for k in ("starts_at", "ends_at", "created_at")}}


def end_maintenance(window_id: int) -> None:
    with get_db() as connection:
        result = connection.execute(update(maintenance_windows).where(maintenance_windows.c.id == window_id)
                                    .values(ends_at=_now()))
    if result.rowcount == 0:
        raise LookupError("Maintenance window not found")


# ------------------------------------------------------------------------------- incidents
def _event(connection, incident_id: int, actor: str, kind: str, message: str | None = None) -> None:
    connection.execute(incident_events.insert().values(incident_id=incident_id, at=_now(), actor=actor, kind=kind,
                                                       message=message))


def _open_incident(connection, fingerprint: str):
    return connection.execute(select(incidents).where(
        incidents.c.fingerprint == fingerprint, incidents.c.status != "resolved")).mappings().first()


def _system(system_id: int | None) -> dict | None:
    if system_id is None:
        return None
    with get_db() as connection:
        row = connection.execute(select(sap_systems).where(sap_systems.c.id == system_id)).mappings().first()
    return dict(row) if row else None


def _notify(incident: dict, kind: str, system: dict | None) -> None:
    """Send open/escalate/resolve notifications unless the system is in maintenance."""
    window = active_maintenance(system)
    with get_db() as connection:
        if window:
            _event(connection, incident["id"], "system", "suppressed",
                   f"Notification suppressed by maintenance window: {window['reason']}")
            return
    label = f"{system['sid']} ({system['landscape']})" if system else "landscape"
    prefix = {"opened": "", "escalated": "ESCALATED ", "resolved": "RESOLVED "}[kind]
    payload = {
        "event": "sap-basis-toolkit.incident",
        "summary": f"[SAP Basis Toolkit] {prefix}{incident['severity'].upper()}: {label}: {incident['title']}",
        "severity": "info" if kind == "resolved" else incident["severity"],
        "details": {"incident": incident["id"], "status": incident["status"], "metric": incident.get("metric"),
                    "value": incident.get("value"), **(json.loads(incident["details"]) if incident.get("details") else {})},
        "occurred_at": _now().isoformat(),
    }
    results = []
    from .. import config
    if config.ALERT_WEBHOOK_URL:
        results.append(("webhook", notifications.send_webhook(payload)))
    if config.SMTP_HOST and config.ALERT_EMAIL_TO:
        body = "\n".join(f"{k}: {v}" for k, v in notifications._facts(payload))
        results.append(("email", notifications.send_email(payload["summary"], body)))
    if config.PAGERDUTY_ROUTING_KEY and (incident["severity"] == "critical" or kind == "resolved"):
        results.append(("pagerduty", notifications.send_pagerduty_action(
            payload, f"incident:{incident['fingerprint']}", incident["severity"],
            "resolve" if kind == "resolved" else "trigger")))
    with get_db() as connection:
        for channel, (ok, error) in results:
            _event(connection, incident["id"], "system", "notified" if ok else "notify_failed",
                   f"{channel}: {kind}" + (f" ({error})" if error else ""))


def upsert(system: dict | None, fingerprint: str, title: str, severity: str, source: str,
           metric: str | None = None, value: float | None = None, details: dict | None = None,
           notify: bool = True) -> dict:
    """Open a new incident or update the open one with the same fingerprint (escalating if needed)."""
    now = _now()
    in_maintenance = active_maintenance(system) is not None
    kind = None
    with get_db() as connection:
        current = _open_incident(connection, fingerprint)
        if current is None:
            incident_id = connection.execute(incidents.insert().values(
                system_id=system["id"] if system else None, fingerprint=fingerprint, source=source, metric=metric,
                title=title, severity=severity, status="open", details=json.dumps(details or {}, default=str),
                value=value, opened_at=now, last_seen_at=now, occurrences=1, in_maintenance=in_maintenance,
            ).returning(incidents.c.id)).scalar_one()
            _event(connection, incident_id, "system", "opened", f"{severity}: {title}")
            kind = "opened"
        else:
            incident_id = current["id"]
            values = {"last_seen_at": now, "occurrences": current["occurrences"] + 1, "value": value, "title": title,
                      "details": json.dumps(details or {}, default=str)}
            if SEVERITY_RANK[severity] > SEVERITY_RANK[current["severity"]]:
                values["severity"] = severity
                values["status"] = "open"  # escalation re-opens an acknowledged incident
                _event(connection, incident_id, "system", "escalated", f"{current['severity']} → {severity}")
                kind = "escalated"
            elif SEVERITY_RANK[severity] < SEVERITY_RANK[current["severity"]]:
                values["severity"] = severity
                _event(connection, incident_id, "system", "deescalated", f"{current['severity']} → {severity}")
            connection.execute(update(incidents).where(incidents.c.id == incident_id).values(**values))
        row = dict(connection.execute(select(incidents).where(incidents.c.id == incident_id)).mappings().one())
    if notify and kind:
        _notify(row, kind, system)
    return row


def auto_resolve(system: dict, fingerprint: str, message: str) -> bool:
    with get_db() as connection:
        current = _open_incident(connection, fingerprint)
        if current is None:
            return False
        connection.execute(update(incidents).where(incidents.c.id == current["id"]).values(
            status="resolved", resolved_by="system", resolved_at=_now(), resolution=message))
        _event(connection, current["id"], "system", "auto_resolved", message)
        row = dict(connection.execute(select(incidents).where(incidents.c.id == current["id"])).mappings().one())
    _notify(row, "resolved", system)
    return True


def evaluate(system: dict, metrics: dict[str, float]) -> dict:
    """Apply rules to fresh metric values; returns counts of opened/updated/resolved incidents."""
    rules = effective_rules(system)
    summary = {"breaches": 0, "resolved": 0}
    for metric, value in metrics.items():
        rule = rules.get(metric)
        if rule is None or value is None:
            continue
        fingerprint = f"rule:{metric}:{system['id']}"
        severity = classify(rule, value)
        if severity:
            threshold = rule["critical"] if severity == "critical" else rule["warning"]
            upsert(system, fingerprint, f"{rule['name']}: {value:g} (threshold {rule['operator']} {threshold:g})",
                   severity, "rule", metric, value,
                   {"rule": rule["name"], "rule_scope": rule["scope_type"], "threshold": threshold, "value": value})
            summary["breaches"] += 1
        elif auto_resolve(system, fingerprint, f"{rule['name']} back to normal ({value:g})"):
            summary["resolved"] += 1
    return summary


def raise_module_incident(system_id: int | None, fingerprint: str, title: str, severity: str,
                          details: dict | None) -> dict:
    """Incident for module alerts (certificates, jobs, locks, transports, growth). Notified by the module."""
    # "error" (e.g. a cancelled job, an RC 8 import) is a warning-level incident; "critical" is reserved for
    # outages and imminent risk (RC >= 12, locks held for hours, certificates about to expire).
    sev = "critical" if severity == "critical" else "warning"
    return upsert(_system(system_id), f"module:{fingerprint}", title, sev, "module", details=details, notify=False)


# ------------------------------------------------------------------------------- queries & actions
def _view(row) -> dict:
    item = dict(row)
    for k in ("opened_at", "last_seen_at", "acknowledged_at", "resolved_at"):
        item[k] = _iso(item[k])
    item["details"] = json.loads(item["details"]) if item.get("details") else {}
    item["in_maintenance"] = bool(item["in_maintenance"])
    return item


def list_incidents(status: str | None = "active", system_id: int | None = None, severity: str | None = None,
                   limit: int = 200) -> list[dict]:
    query = (select(incidents, sap_systems.c.sid, sap_systems.c.landscape)
             .join(sap_systems, sap_systems.c.id == incidents.c.system_id, isouter=True)
             .order_by(incidents.c.last_seen_at.desc(), incidents.c.id.desc()).limit(limit))
    if status == "active":
        query = query.where(incidents.c.status != "resolved")
    elif status:
        query = query.where(incidents.c.status == status)
    if system_id is not None:
        query = query.where(incidents.c.system_id == system_id)
    if severity:
        query = query.where(incidents.c.severity == severity)
    with get_db() as connection:
        rows = connection.execute(query).mappings().all()
    items = [_view(r) for r in rows]
    items.sort(key=lambda i: (i["status"] == "resolved", -SEVERITY_RANK[i["severity"]], i["status"] != "open"))
    return items


def get_incident(incident_id: int) -> dict:
    with get_db() as connection:
        row = connection.execute(
            select(incidents, sap_systems.c.sid, sap_systems.c.landscape)
            .join(sap_systems, sap_systems.c.id == incidents.c.system_id, isouter=True)
            .where(incidents.c.id == incident_id)).mappings().first()
        if row is None:
            raise LookupError("Incident not found")
        events = connection.execute(select(incident_events).where(incident_events.c.incident_id == incident_id)
                                    .order_by(incident_events.c.at, incident_events.c.id)).mappings().all()
    item = _view(row)
    item["timeline"] = [{**dict(e), "at": _iso(e["at"])} for e in events]
    return item


def act(incident_id: int, action: str, actor: str, message: str | None = None, assignee: str | None = None) -> dict:
    with get_db() as connection:
        row = connection.execute(select(incidents).where(incidents.c.id == incident_id)).mappings().first()
        if row is None:
            raise LookupError("Incident not found")
        now = _now()
        if action == "acknowledge":
            if row["status"] != "open":
                raise ValueError(f"Only open incidents can be acknowledged (status: {row['status']})")
            connection.execute(update(incidents).where(incidents.c.id == incident_id).values(
                status="acknowledged", acknowledged_by=actor, acknowledged_at=now))
            _event(connection, incident_id, actor, "acknowledged", message)
        elif action == "resolve":
            if row["status"] == "resolved":
                raise ValueError("Incident is already resolved")
            connection.execute(update(incidents).where(incidents.c.id == incident_id).values(
                status="resolved", resolved_by=actor, resolved_at=now, resolution=message))
            _event(connection, incident_id, actor, "resolved", message)
        elif action == "reopen":
            if row["status"] != "resolved":
                raise ValueError("Only resolved incidents can be reopened")
            connection.execute(update(incidents).where(incidents.c.id == incident_id).values(
                status="open", resolved_by=None, resolved_at=None, resolution=None, last_seen_at=now))
            _event(connection, incident_id, actor, "reopened", message)
        elif action == "assign":
            connection.execute(update(incidents).where(incidents.c.id == incident_id).values(assignee=assignee or None))
            _event(connection, incident_id, actor, "assigned", f"assigned to {assignee}" if assignee else "unassigned")
        elif action == "comment":
            if not (message or "").strip():
                raise ValueError("Comment text is required")
            _event(connection, incident_id, actor, "comment", message.strip())
        else:
            raise ValueError("Unknown action")
    return get_incident(incident_id)


def _empty_counts() -> dict:
    return {"open": 0, "acknowledged": 0, "critical": 0, "warning": 0, "total": 0}


def counts_by_system() -> dict[int | None, dict]:
    """Active incident counts for every system in one aggregate query (dashboards over the whole fleet)."""
    query = (select(incidents.c.system_id, incidents.c.status, incidents.c.severity, func.count().label("n"))
             .where(incidents.c.status != "resolved")
             .group_by(incidents.c.system_id, incidents.c.status, incidents.c.severity))
    out: dict[int | None, dict] = {}
    with get_db() as connection:
        for system_id, status, severity, n in connection.execute(query):
            c = out.setdefault(system_id, _empty_counts())
            if status in ("open", "acknowledged"):
                c[status] += n
            if severity in ("critical", "warning"):
                c[severity] += n
            c["total"] += n
    return out


def counts(system_id: int | None = None) -> dict:
    query = (select(incidents.c.status, incidents.c.severity, func.count().label("n"))
             .where(incidents.c.status != "resolved").group_by(incidents.c.status, incidents.c.severity))
    if system_id is not None:
        query = query.where(incidents.c.system_id == system_id)
    c = _empty_counts()
    with get_db() as connection:
        for status, severity, n in connection.execute(query):
            if status in ("open", "acknowledged"):
                c[status] += n
            if severity in ("critical", "warning"):
                c[severity] += n
            c["total"] += n
    return c


def worst_severity(c: dict) -> str | None:
    return "critical" if c.get("critical") else "warning" if c.get("warning") else None


def worst_active_severity(system_id: int) -> str | None:
    return worst_severity(counts(system_id))

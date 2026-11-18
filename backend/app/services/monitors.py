"""Normalised live monitors, metric samples and export-upload fallbacks.

Every monitor is stored as a snapshot (JSON) with a health, and contributes metric samples that
the incident rules evaluate. Sources: sapcontrol, HANA SQL, uploads (ST22, SM21, SM13, SM58,
SMQ1/SMQ2, WE02) or the demo simulator - the same builders are used for all of them.
"""
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from .. import config
from ..db import get_db, metric_points, monitor_snapshots
from .tabular import InputFormatError, parse_number, parse_sap_datetime, pick, read_rows, require_columns

MONITORS = ("availability", "workprocesses", "dumps", "syslog", "updates", "trfc", "qrfc", "idocs", "hana", "host")
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
        connection.execute(monitor_snapshots.insert().values(
            system_id=system_id, monitor=monitor, source=source, captured_at=captured_at, health=health,
            data=json.dumps(data, default=str)))


def record_metrics(system_id: int, metrics: dict[str, float], ts: datetime | None = None) -> None:
    ts = ts or _now()
    rows = [{"system_id": system_id, "metric": k, "value": float(v), "ts": ts} for k, v in metrics.items() if v is not None]
    if rows:
        with get_db() as connection:
            connection.execute(metric_points.insert(), rows)


def latest(system_id: int, monitor: str | None = None) -> dict:
    """Latest snapshot per monitor: {monitor: {source, captured_at, health, stale, data}}."""
    with get_db() as connection:
        newest = select(monitor_snapshots.c.monitor, func.max(monitor_snapshots.c.id).label("id")).where(
            monitor_snapshots.c.system_id == system_id).group_by(monitor_snapshots.c.monitor).subquery()
        query = select(monitor_snapshots).join(newest, newest.c.id == monitor_snapshots.c.id)
        if monitor:
            query = query.where(monitor_snapshots.c.monitor == monitor)
        rows = connection.execute(query).mappings().all()
    stale_before = _now() - timedelta(hours=config.OPS_DATA_STALE_HOURS)
    result = {}
    for r in rows:
        captured = datetime.fromisoformat(_iso(r["captured_at"]))
        result[r["monitor"]] = {"source": r["source"], "captured_at": captured.isoformat(), "health": r["health"],
                                "stale": captured < stale_before, "data": json.loads(r["data"])}
    return result


def series(system_id: int, metrics: list[str], hours: int = 24) -> dict[str, list[dict]]:
    since = _now() - timedelta(hours=hours)
    with get_db() as connection:
        rows = connection.execute(select(metric_points.c.metric, metric_points.c.value, metric_points.c.ts).where(
            metric_points.c.system_id == system_id, metric_points.c.metric.in_(metrics), metric_points.c.ts >= since,
        ).order_by(metric_points.c.ts)).all()
    out: dict[str, list[dict]] = {m: [] for m in metrics}
    for metric, value, ts in rows:
        out[metric].append({"ts": _iso(ts), "value": value})
    return out


def prune() -> None:
    with get_db() as connection:
        connection.execute(delete(metric_points).where(
            metric_points.c.ts < _now() - timedelta(days=config.METRIC_RETENTION_DAYS)))
        # keep the newest snapshot per system/monitor even when old
        keep = select(func.max(monitor_snapshots.c.id)).group_by(monitor_snapshots.c.system_id, monitor_snapshots.c.monitor)
        connection.execute(delete(monitor_snapshots).where(
            monitor_snapshots.c.captured_at < _now() - timedelta(days=config.SNAPSHOT_RETENTION_DAYS),
            monitor_snapshots.c.id.notin_(keep)))


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
            "service_threads": threads, "expensive_statements_1h": expensive}
    return data, metrics, _health(levels)


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

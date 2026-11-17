"""Collector configuration, execution and scheduling."""
import json
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from ... import config
from ...db import collectors, get_db, sap_systems
from .. import growth, incidents, monitors, ops, transports, vault
from ..systems import system_zone
from ..tabular import parse_sap_datetime
from . import CollectorError, hana, sapcontrol, simulator, validate_port

logger = logging.getLogger(__name__)
TYPES = ("sapcontrol", "hana")


def _iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def view(row) -> dict:
    """Collector as returned by the API: never includes the password."""
    item = {k: row[k] for k in ("id", "system_id", "type", "host", "port", "use_tls", "verify_tls", "username",
                                "database_name", "abap_schema", "interval_seconds", "enabled", "last_status",
                                "last_error", "last_duration_ms")}
    for k in ("use_tls", "verify_tls", "enabled"):
        item[k] = bool(item[k])
    item["has_password"] = bool(row["password_enc"])
    item["last_run_at"] = _iso(row["last_run_at"])
    item["created_at"] = _iso(row["created_at"])
    item["diagnostics"] = json.loads(row["last_details"]) if row["last_details"] else []
    item["simulated"] = row["host"] == simulator.SIM_HOST
    return item


def validate(data: dict) -> None:
    if data["type"] not in TYPES:
        raise CollectorError(f"type must be one of {', '.join(TYPES)}")
    if data["host"] == simulator.SIM_HOST:
        if not config.SIMULATOR_ENABLED:
            raise CollectorError("The demo simulator is disabled (SIMULATOR_ENABLED=false)")
    else:
        validate_port(data["type"], int(data["port"]))
    if data["type"] == "hana" and data["host"] != simulator.SIM_HOST and not data.get("username"):
        raise CollectorError("HANA collectors need a database user")
    if data.get("abap_schema") and not hana.SCHEMA_RE.match(data["abap_schema"]):
        raise CollectorError("ABAP schema must be an uppercase identifier, e.g. SAPHANADB")
    if not 30 <= int(data.get("interval_seconds") or 300) <= 86400:
        raise CollectorError("interval_seconds must be between 30 and 86400")


def list_for_system(system_id: int) -> list[dict]:
    with get_db() as connection:
        rows = connection.execute(select(collectors).where(collectors.c.system_id == system_id)
                                  .order_by(collectors.c.type, collectors.c.id)).mappings().all()
    return [view(r) for r in rows]


def get(collector_id: int) -> dict:
    with get_db() as connection:
        row = connection.execute(select(collectors).where(collectors.c.id == collector_id)).mappings().first()
    if row is None:
        raise LookupError("Collector not found")
    return dict(row)


def create(system_id: int, data: dict) -> dict:
    data = {**data, "host": data["host"].strip().lower(), "abap_schema": (data.get("abap_schema") or "").strip().upper() or None}
    validate(data)
    password = data.pop("password", None)
    with get_db() as connection:
        row = connection.execute(collectors.insert().values(
            system_id=system_id, password_enc=vault.encrypt(password), **data).returning(collectors)).mappings().one()
    return view(row)


def update_collector(collector_id: int, changes: dict) -> dict:
    current = get(collector_id)
    if "host" in changes and changes["host"]:
        changes["host"] = changes["host"].strip().lower()
    if "abap_schema" in changes:
        changes["abap_schema"] = (changes["abap_schema"] or "").strip().upper() or None
    password = changes.pop("password", None)
    clear_password = changes.pop("clear_password", False)
    validate({**current, **changes})
    if password:
        changes["password_enc"] = vault.encrypt(password)
    elif clear_password:
        changes["password_enc"] = None
    with get_db() as connection:
        connection.execute(update(collectors).where(collectors.c.id == collector_id).values(**changes))
    return view(get(collector_id))


def delete(collector_id: int) -> None:
    get(collector_id)
    with get_db() as connection:
        connection.execute(collectors.delete().where(collectors.c.id == collector_id))


# ------------------------------------------------------------------------------- execution
def _system(system_id: int) -> dict:
    with get_db() as connection:
        row = connection.execute(select(sap_systems).where(sap_systems.c.id == system_id)).mappings().one()
    return dict(row)


def _seed(system: dict) -> str:
    return f"{system['sid']}:{system['landscape']}:{system['id']}"


def _run_sapcontrol(row: dict, system: dict, persist: bool) -> tuple[dict, dict]:
    zone = system_zone(system)
    endpoint = sapcontrol.Endpoint(row["host"], row["port"], bool(row["use_tls"]), bool(row["verify_tls"]),
                                   row["username"], vault.decrypt(row["password_enc"]))
    transport = (simulator.sapcontrol_transport(_seed(system), java=system["stack"] == "JAVA") if row["host"] == simulator.SIM_HOST
                 else sapcontrol.http_transport)
    raw = sapcontrol.collect(sapcontrol.SapControlClient(endpoint, transport), zone)
    source = "simulator" if row["host"] == simulator.SIM_HOST else "sapcontrol"
    metrics: dict[str, float] = {}
    summary: dict = {}
    data, m, h = monitors.build_availability(raw["instances"])
    metrics.update(m)
    summary["availability"] = data["summary"]
    if persist:
        monitors.store(system["id"], "availability", source, data, h, raw["collected_at"])
    if raw["workprocesses"]:
        data, m, h = monitors.build_workprocesses(raw["workprocesses"])
        metrics.update(m)
        summary["workprocesses"] = data["summary"]
        if persist:
            monitors.store(system["id"], "workprocesses", source, data, h, raw["collected_at"])
    if raw["syslog_supported"]:
        data, m, h = monitors.build_syslog(raw["syslog"])
        metrics.update(m)
        summary["syslog"] = data["summary"]
        if persist:
            monitors.store(system["id"], "syslog", source, data, h, raw["collected_at"])
    return {"metrics": metrics, "summary": summary}, {"diagnostics": raw["diagnostics"]}


def _run_hana(row: dict, system: dict, persist: bool) -> tuple[dict, dict]:
    zone = system_zone(system)
    if row["host"] == simulator.SIM_HOST:
        client, source = simulator.SimulatedHana(_seed(system)), "simulator"
    else:
        client = hana.HanaClient(row["host"], row["port"], row["username"] or "", vault.decrypt(row["password_enc"]) or "",
                                 bool(row["use_tls"]), bool(row["verify_tls"]))
        source = "hana"
    try:
        raw = hana.collect(client, row["abap_schema"], zone)
    finally:
        client.close()
    if not any(d["ok"] for d in raw["diagnostics"]):
        first = next((d.get("error") for d in raw["diagnostics"] if not d["ok"]), "no query succeeded")
        raise CollectorError(f"HANA queries failed: {first}")
    at = raw["collected_at"]
    metrics: dict[str, float] = {}
    summary: dict = {}

    def emit(monitor, built):
        data, m, h = built
        metrics.update(m)
        summary[monitor] = data.get("summary", {k: data.get(k) for k in ("memory", "cpu_pct", "disk")})
        if persist:
            monitors.store(system["id"], monitor, source, data, h, at)

    if raw["hana"]:
        emit("hana", monitors.build_hana(raw["hana"]))
    if "dumps" in raw:
        emit("dumps", monitors.build_dumps(raw["dumps"]))
    if "updates" in raw:
        emit("updates", monitors.build_updates(raw["updates"]))
    if "trfc" in raw:
        emit("trfc", monitors.build_trfc(raw["trfc"]))
    if "qrfc" in raw:
        emit("qrfc", monitors.build_qrfc(raw["qrfc"]))
    if "idocs" in raw:
        emit("idocs", monitors.build_idocs(raw["idocs"]["errors"], raw["idocs"]["total"]))
    if persist and raw.get("jobs") is not None:
        jobs = ops.parse_job_rows(raw["jobs"], zone, at)
        summary["jobs_synced"] = ops.store_job_list(system, jobs, at)
    if persist and raw.get("imports") is not None:
        grouped: dict[tuple, dict] = {}
        for r in raw["imports"]:
            when = parse_sap_datetime(str(r.get("TRTIME") or ""), "", zone)
            rc_raw = str(r.get("RETCODE") or "0").strip()
            if not when or not rc_raw.isdigit():
                continue
            key = (str(r["TRKORR"]).upper(), when)
            entry = grouped.setdefault(key, {"trkorr": key[0], "rc": 0, "imported_at": when, "time_known": True,
                                             "owner": None, "description": None})
            entry["rc"] = max(entry["rc"], int(rc_raw))
        entries = [{**e, "result": transports.classify_rc(e["rc"])} for e in grouped.values()]
        summary["imports_synced"] = transports.store_import_history(system["id"], entries)
    if persist and raw.get("table_sizes"):
        last = growth.last_snapshot_at(system["id"])
        if last is None or last < at - timedelta(hours=config.HANA_GROWTH_SNAPSHOT_HOURS):
            entries = [{"table_name": str(r["TABLE_NAME"]).upper(), "size_mb": float(r["MEMORY_SIZE_IN_TOTAL"] or 0) / 1048576,
                        "row_count": int(r["RECORD_COUNT"] or 0)} for r in raw["table_sizes"]]
            growth.store_entries(system["id"], entries, at)
            disk, mem = raw["hana"].get("disk"), raw["hana"].get("memory")
            if disk:
                growth.store_capacity(system["id"], disk["total"] / 1048576, disk["used"] / 1048576,
                                      mem["limit"] / 1048576 if mem else None, mem["used"] / 1048576 if mem else None, at)
            summary["growth_snapshot"] = len(entries)
    return {"metrics": metrics, "summary": summary}, {"diagnostics": raw["diagnostics"]}


def run(collector_id: int, persist: bool = True) -> dict:
    """Run one collector now. With persist=False it is a connection test (nothing stored or alerted)."""
    row = get(collector_id)
    system = _system(row["system_id"])
    started = time.monotonic()
    runner = _run_sapcontrol if row["type"] == "sapcontrol" else _run_hana
    try:
        result, details = runner(row, system, persist)
        status, error = "ok", None
        failed_calls = [d for d in details["diagnostics"] if not d["ok"]]
        if failed_calls:
            status = "partial"
            error = "; ".join(f"{d.get('instance', '')} {d['call']}: {d.get('error')}".strip() for d in failed_calls[:5])
        metrics = {**result["metrics"], "collector.failed": 0.0}
    except (CollectorError, ValueError) as exc:
        result, details = {"metrics": {}, "summary": {}}, {"diagnostics": []}
        status, error = "error", str(exc)[:1000]
        metrics = {"collector.failed": 1.0}
    except Exception as exc:  # unexpected: keep the scheduler alive, surface the error
        logger.exception("Collector %s failed", collector_id)
        result, details = {"metrics": {}, "summary": {}}, {"diagnostics": []}
        status, error = "error", f"Unexpected error: {exc}"[:1000]
        metrics = {"collector.failed": 1.0}
    duration = int((time.monotonic() - started) * 1000)
    evaluation = None
    if persist:
        monitors.record_metrics(system["id"], metrics)
        evaluation = incidents.evaluate(system, metrics)
        with get_db() as connection:
            connection.execute(update(collectors).where(collectors.c.id == collector_id).values(
                last_run_at=datetime.now(timezone.utc), last_status=status, last_error=error, last_duration_ms=duration,
                last_details=json.dumps(details["diagnostics"], default=str)[:20000]))
    return {"collector_id": collector_id, "status": status, "error": error, "duration_ms": duration,
            "summary": result["summary"], "metrics": metrics, "diagnostics": details["diagnostics"], "incidents": evaluation}


def test_connection(system_id: int, data: dict) -> dict:
    """Try an unsaved configuration (connectivity and authorisation) without storing anything."""
    data = {**data, "host": data["host"].strip().lower(), "abap_schema": (data.get("abap_schema") or "").strip().upper() or None}
    validate(data)
    system = _system(system_id)
    row = {**data, "id": 0, "system_id": system_id, "password_enc": vault.encrypt(data.get("password"))}
    runner = _run_sapcontrol if data["type"] == "sapcontrol" else _run_hana
    started = time.monotonic()
    try:
        result, details = runner(row, system, persist=False)
        return {"ok": True, "duration_ms": int((time.monotonic() - started) * 1000), "summary": result["summary"],
                "diagnostics": details["diagnostics"]}
    except (CollectorError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "duration_ms": int((time.monotonic() - started) * 1000)}


def due_collectors(now: datetime | None = None) -> list[int]:
    now = now or datetime.now(timezone.utc)
    with get_db() as connection:
        rows = connection.execute(
            select(collectors.c.id, collectors.c.last_run_at, collectors.c.interval_seconds)
            .join(sap_systems, sap_systems.c.id == collectors.c.system_id)
            .where(collectors.c.enabled.is_(True), sap_systems.c.enabled.is_(True))).all()
    due = []
    for cid, last, interval in rows:
        last = datetime.fromisoformat(_iso(last)) if last else None
        if last is None or (now - last).total_seconds() >= interval:
            due.append(cid)
    return due


def run_due() -> list[dict]:
    ids = due_collectors()
    if not ids:
        return []
    with ThreadPoolExecutor(max_workers=max(1, config.COLLECTOR_MAX_PARALLEL)) as pool:
        return list(pool.map(run, ids))


def status_by_system() -> dict[int, list[dict]]:
    with get_db() as connection:
        rows = connection.execute(select(collectors)).mappings().all()
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["system_id"]].append(view(r))
    return out

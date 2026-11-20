"""Collector configuration, execution and scheduling."""
import json
import logging
import threading
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
from . import CollectorError, ssh, hana, node_exporter, sapcontrol, simulator, validate_port

logger = logging.getLogger(__name__)
TYPES = ("sapcontrol", "hana", "hana_sapcontrol", "node_exporter", "os_ssh")
# os_ssh: read-only SSH inventory of an application or database host (OS layer).
# hana_sapcontrol: the HANA instance's own sapstartsrv (5<NN>13/14, <hanasid>adm) for DB availability and start/stop.


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
    item["has_private_key"] = bool(row.get("private_key_enc"))
    item["host_key"] = row.get("host_key")
    item["last_run_at"] = _iso(row["last_run_at"])
    item["created_at"] = _iso(row["created_at"])
    item["diagnostics"] = json.loads(row["last_details"]) if row["last_details"] else []
    item["simulated"] = simulator.is_sim(row["host"])
    return item


def validate(data: dict) -> None:
    if data["type"] not in TYPES:
        raise CollectorError(f"type must be one of {', '.join(TYPES)}")
    if simulator.is_sim(data["host"]):
        if not config.SIMULATOR_ENABLED:
            raise CollectorError("The demo simulator is disabled (SIMULATOR_ENABLED=false)")
    elif data["type"] == "os_ssh":
        allowed = {int(p) for p in str(config.SSH_ALLOWED_PORTS).split(",") if p.strip()}
        if int(data["port"]) not in allowed:
            raise CollectorError(f"SSH port {data['port']} is not allowed (SSH_ALLOWED_PORTS={config.SSH_ALLOWED_PORTS})")
    else:
        validate_port("sapcontrol" if data["type"] == "hana_sapcontrol" else data["type"], int(data["port"]))
    if data["type"] == "os_ssh" and not data.get("username"):
        raise CollectorError("OS inventory needs an OS user (root or <sid>adm)")
    if data["type"] == "hana" and not simulator.is_sim(data["host"]) and not data.get("username"):
        raise CollectorError("HANA collectors need a database user")
    if data["type"] == "node_exporter" and simulator.is_sim(data["host"]):
        raise CollectorError("node_exporter does not support the SAP demo simulator")
    if data["type"] == "node_exporter" and (data.get("username") or data.get("password")):
        raise CollectorError("node_exporter credentials are not accepted; secure the endpoint at the network or reverse-proxy layer")
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
    if data["type"] == "node_exporter":
        with get_db() as connection:
            existing = connection.execute(select(collectors.c.id).where(
                collectors.c.system_id == system_id, collectors.c.type == "node_exporter"
            )).first()
        if existing:
            raise CollectorError("Only one node_exporter collector per SAP system is supported until host-level metric dimensions are available")
    password = data.pop("password", None)
    private_key = data.pop("private_key", None)
    if data["type"] == "os_ssh" and not (password or private_key):
        raise CollectorError("OS inventory needs the OS password or a private key")
    with get_db() as connection:
        row = connection.execute(collectors.insert().values(
            system_id=system_id, password_enc=vault.encrypt(password), private_key_enc=vault.encrypt(private_key),
            **data).returning(collectors)).mappings().one()
    return view(row)


def update_collector(collector_id: int, changes: dict) -> dict:
    current = get(collector_id)
    if "host" in changes and changes["host"]:
        changes["host"] = changes["host"].strip().lower()
    if "abap_schema" in changes:
        changes["abap_schema"] = (changes["abap_schema"] or "").strip().upper() or None
    password = changes.pop("password", None)
    clear_password = changes.pop("clear_password", False)
    private_key = changes.pop("private_key", None)
    if changes.pop("trust_new_host_key", False):
        changes["host_key"] = None  # the next successful connection pins the new key
    if current["type"] == "node_exporter" and (password or changes.get("username")):
        raise CollectorError("node_exporter credentials are not accepted; secure the endpoint at the network or reverse-proxy layer")
    validate({**current, **changes})
    if password:
        changes["password_enc"] = vault.encrypt(password)
    elif clear_password:
        changes["password_enc"] = None
    if private_key:
        changes["private_key_enc"] = vault.encrypt(private_key)
    changes = {k: v for k, v in changes.items() if k in collectors.c}
    if changes:
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
    transport = (simulator.sapcontrol_transport(simulator.seed_for(row["host"], _seed(system)), java=system["stack"] == "JAVA") if simulator.is_sim(row["host"])
                 else sapcontrol.http_transport)
    raw = sapcontrol.collect(sapcontrol.SapControlClient(endpoint, transport), zone)
    source = "simulator" if simulator.is_sim(row["host"]) else "sapcontrol"
    metrics: dict[str, float] = {}
    summary: dict = {}
    data, m, h = monitors.build_availability(raw["instances"])
    metrics.update(m)
    summary["availability"] = data["summary"]
    if persist:
        monitors.store(system["id"], "availability", source, data, h, raw["collected_at"])
        from .. import discovery
        discovery.sync_from_collection(system["id"], raw["instances"])
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


def sapcontrol_client(row: dict, system: dict) -> sapcontrol.SapControlClient:
    endpoint = sapcontrol.Endpoint(row["host"], row["port"], bool(row["use_tls"]), bool(row["verify_tls"]),
                                   row["username"], vault.decrypt(row["password_enc"]))
    transport = (simulator.sapcontrol_transport(simulator.seed_for(row["host"], _seed(system)), java=system["stack"] == "JAVA")
                 if simulator.is_sim(row["host"]) else sapcontrol.http_transport)
    return sapcontrol.SapControlClient(endpoint, transport)


def hana_client(row: dict, system: dict):
    """SQL client for a 'hana' collector row (simulated for simulator hosts)."""
    if simulator.is_sim(row["host"]):
        return simulator.SimulatedHana(simulator.seed_for(row["host"], _seed(system)))
    return hana.HanaClient(row["host"], row["port"], row["username"] or "", vault.decrypt(row["password_enc"]) or "",
                           bool(row["use_tls"]), bool(row["verify_tls"]))


def _run_hana_sapcontrol(row: dict, system: dict, persist: bool) -> tuple[dict, dict]:
    """HANA instance availability from the database's own sapstartsrv."""
    client = sapcontrol_client(row, system)
    raw = client.call("GetSystemInstanceList")
    if not raw:
        raise CollectorError("GetSystemInstanceList returned no HANA instances")
    diagnostics, instances = [], []
    for inst in raw:
        name = f"{(inst.get('hostname') or '').lower()}_{str(inst.get('instanceNr', '')).zfill(2)}"
        status = (inst.get("dispstatus") or "").replace("SAPControl-", "")
        entry = {"hostname": (inst.get("hostname") or "").lower(), "instance_nr": str(inst.get("instanceNr", "")).zfill(2),
                 "features": inst.get("features", ""), "status": status}
        try:
            procs = client.call("GetProcessList", endpoint=client.instance_endpoint(inst))
            entry["processes"] = [{"name": p.get("name"), "status": (p.get("dispstatus") or "").replace("SAPControl-", "")} for p in procs]
            diagnostics.append({"instance": name, "call": "GetProcessList", "ok": True, "rows": len(procs)})
        except CollectorError as exc:
            diagnostics.append({"instance": name, "call": "GetProcessList", "ok": False, "error": str(exc)})
        instances.append(entry)
    down = sum(1 for i in instances if i["status"] != "GREEN")
    metrics = {"hana.system_up": 0.0 if down else 1.0, "hana.instances_down": float(down)}
    if persist:
        from .. import discovery
        discovery.sync_from_collection(system["id"], instances, force_role="HANA")
    return {"metrics": metrics, "summary": {"hana_instances": len(instances), "hana_down": down}}, {"diagnostics": diagnostics}


def _run_hana(row: dict, system: dict, persist: bool) -> tuple[dict, dict]:
    zone = system_zone(system)
    client = hana_client(row, system)
    source = "simulator" if simulator.is_sim(row["host"]) else "hana"
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


def _run_node_exporter(row: dict, system: dict, persist: bool) -> tuple[dict, dict]:
    raw = node_exporter.collect(row["host"], int(row["port"]), bool(row["use_tls"]), bool(row["verify_tls"]))
    metrics = raw["metrics"]
    summary = {"host": row["host"], "signals": sorted(metrics), "collected_at": raw["collected_at"].isoformat()}
    if persist:
        health = "critical" if metrics.get("host.memory_used_pct", 0) >= 95 or metrics.get("host.disk_used_pct", 0) >= 95 else "warning" if metrics.get("host.memory_used_pct", 0) >= 85 or metrics.get("host.disk_used_pct", 0) >= 85 else "healthy"
        monitors.store(system["id"], "host", "node_exporter", {"metrics": metrics, "summary": summary}, health, raw["collected_at"])
    return {"metrics": metrics, "summary": summary}, {"diagnostics": [{"call": "GET /metrics", "ok": True, "signals": sorted(metrics)}]}


def ssh_target(row: dict) -> "ssh.SshTarget":
    return ssh.SshTarget(row["host"], int(row["port"]), row["username"] or "", vault.decrypt(row["password_enc"]),
                         vault.decrypt(row.get("private_key_enc")), row.get("host_key"))


DB_MOUNTS = ("/hana", "/oracle", "/db2", "/sybase", "/sapdb")
APP_MOUNTS = ("/", "/usr/sap", "/sapmnt")


def _run_os_ssh(row: dict, system: dict, persist: bool) -> tuple[dict, dict]:
    """Refresh the OS layer of one host: facts, current utilisation and (on the database host) database processes."""
    raw = ssh.utilisation(ssh_target(row))
    os_info = raw["os"]
    disks = os_info.get("filesystems") or []
    role = raw.get("role") or "application"
    if persist:
        from .. import discovery
        role = discovery.update_host_asset(system, row["host"], os_info, raw["host_key"], role) or role
        if not row.get("host_key") and raw["host_key"]:
            with get_db() as connection:  # first successful contact pins the key (TOFU)
                connection.execute(update(collectors).where(collectors.c.id == row["id"]).values(host_key=raw["host_key"]))
    metrics: dict[str, float] = {}
    diagnostics = [{"call": "ssh inventory", "ok": True, "host_key": raw["host_key"], "role": role}]
    is_app = "application" in role
    is_db = "database" in role
    # host.* has no host dimension yet: only the application host feeds it, and only without node_exporter.
    if is_app:
        with get_db() as connection:
            has_node = connection.execute(select(collectors.c.id).where(collectors.c.system_id == system["id"],
                                                                      collectors.c.type == "node_exporter")).first()
        if not has_node:
            app_disk = max((d["used_pct"] for d in disks if d["mount"] in APP_MOUNTS), default=None)
            metrics.update({k: v for k, v in {"host.memory_used_pct": os_info.get("memory_used_pct"), "host.load1": os_info.get("load1"),
                                               "host.disk_used_pct": app_disk}.items() if v is not None})
    if is_db:
        db_disk = max((d["used_pct"] for d in disks if d["mount"].startswith(DB_MOUNTS)), default=None)
        metrics.update({k: v for k, v in {"db.host_memory_used_pct": os_info.get("memory_used_pct"), "db.host_load1": os_info.get("load1"),
                                           "db.host_disk_used_pct": db_disk}.items() if v is not None})
        running = ssh.database_running(os_info, system.get("database"))
        if running is not None:
            metrics["db.os_process_up"] = 1.0 if running else 0.0
            diagnostics.append({"call": "database process", "ok": True, "running": running})
        else:
            diagnostics.append({"call": "database process", "ok": True, "running": None,
                                "note": f"No process check for {system.get('database')}"})
    summary = {"host": row["host"], "role": role, "os": os_info.get("os_pretty"), "kernel": os_info.get("kernel"),
               "memory_used_pct": os_info.get("memory_used_pct"), "load1": os_info.get("load1"),
               "disk_used_pct": max((d["used_pct"] for d in disks), default=None), "processes": os_info.get("processes")}
    return {"metrics": metrics, "summary": summary}, {"diagnostics": diagnostics}


RUNNERS = {"sapcontrol": _run_sapcontrol, "hana": _run_hana, "hana_sapcontrol": _run_hana_sapcontrol,
           "node_exporter": _run_node_exporter, "os_ssh": _run_os_ssh}


def run(collector_id: int, persist: bool = True) -> dict:
    """Run one collector now. With persist=False it is a connection test (nothing stored or alerted)."""
    row = get(collector_id)
    system = _system(row["system_id"])
    started = time.monotonic()
    runner = RUNNERS[row["type"]]
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
    runner = RUNNERS[data["type"]]
    started = time.monotonic()
    try:
        result, details = runner(row, system, persist=False)
        return {"ok": True, "duration_ms": int((time.monotonic() - started) * 1000), "summary": result["summary"],
                "diagnostics": details["diagnostics"]}
    except (CollectorError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "duration_ms": int((time.monotonic() - started) * 1000)}


def due_collectors(now: datetime | None = None) -> list[int]:
    """Enabled collectors whose interval has elapsed, most overdue first (never-run collectors first of all)."""
    now = now or datetime.now(timezone.utc)
    with get_db() as connection:
        rows = connection.execute(
            select(collectors.c.id, collectors.c.last_run_at, collectors.c.interval_seconds)
            .join(sap_systems, sap_systems.c.id == collectors.c.system_id)
            .where(collectors.c.enabled.is_(True), sap_systems.c.enabled.is_(True))).all()
    due = []
    for cid, last, interval in rows:
        last = datetime.fromisoformat(_iso(last)) if last else None
        overdue = float("inf") if last is None else (now - last).total_seconds() - interval
        if overdue >= 0:
            due.append((overdue, cid))
    return [cid for _, cid in sorted(due, key=lambda d: (-d[0], d[1]))]


def run_due() -> list[dict]:
    """Run every due collector and wait for all of them (tests, CLI). The scheduler uses `Dispatcher` instead."""
    ids = due_collectors()
    if not ids:
        return []
    with ThreadPoolExecutor(max_workers=max(1, config.COLLECTOR_MAX_PARALLEL)) as pool:
        return list(pool.map(run, ids))


class Dispatcher:
    """Non-blocking collector scheduling for the worker process.

    Each tick hands due collectors to a long-lived thread pool and returns at once, so a slow or unreachable SAP system
    never delays the others. A collector that is still queued or running is not submitted again (no overlapping runs of
    the same collector), and the most overdue collectors are queued first."""

    def __init__(self, workers: int | None = None):
        self.workers = max(1, workers or config.COLLECTOR_MAX_PARALLEL)
        self.pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="collector")
        self._lock = threading.Lock()
        self._inflight: set[int] = set()
        self.started = self.completed = self.failed = 0
        self._busy_ticks = 0

    def _done(self, cid: int, future) -> None:
        with self._lock:
            self._inflight.discard(cid)
            self.completed += 1
            if future.exception() is not None:
                self.failed += 1
                logger.error("Collector %s crashed: %s", cid, future.exception())

    def tick(self) -> int:
        """Submit due collectors that are not already queued or running; returns how many were submitted."""
        submitted = 0
        for cid in due_collectors():
            with self._lock:
                if cid in self._inflight:
                    continue
                self._inflight.add(cid)
            future = self.pool.submit(run, cid)
            future.add_done_callback(lambda f, cid=cid: self._done(cid, f))
            submitted += 1
        with self._lock:
            self.started += submitted
            backlog = len(self._inflight)
        # a burst after start-up is normal; warn only when the backlog persists
        self._busy_ticks = self._busy_ticks + 1 if backlog > self.workers * 4 else 0
        if self._busy_ticks == 8:
            logger.warning("Collector backlog: %s collectors queued or running on %s workers; raise COLLECTOR_MAX_PARALLEL "
                           "or lengthen collector intervals", backlog, self.workers)
        return submitted

    def inflight(self) -> int:
        with self._lock:
            return len(self._inflight)

    def shutdown(self, wait: bool = True) -> None:
        self.pool.shutdown(wait=wait, cancel_futures=not wait)


def scheduling_health(now: datetime | None = None) -> dict:
    """How far collection is behind schedule, computed from the database (works from any API process)."""
    now = now or datetime.now(timezone.utc)
    with get_db() as connection:
        rows = connection.execute(
            select(collectors.c.id, collectors.c.last_run_at, collectors.c.interval_seconds, collectors.c.last_status)
            .join(sap_systems, sap_systems.c.id == collectors.c.system_id)
            .where(collectors.c.enabled.is_(True), sap_systems.c.enabled.is_(True))).all()
    lags, late, never, failing = [], 0, 0, 0
    for _, last, interval, status in rows:
        if last is None:
            never += 1
            continue
        lag = (now - datetime.fromisoformat(_iso(last))).total_seconds() - interval
        lags.append(max(0.0, lag))
        if lag > max(2 * config.COLLECTOR_TICK_SECONDS, interval):  # missed more than a whole interval
            late += 1
        failing += status == "error"
    return {"collectors": len(rows), "never_run": never, "late": late, "failing": failing,
            "max_lag_s": round(max(lags), 1) if lags else None, "workers": config.COLLECTOR_MAX_PARALLEL,
            "status": "behind" if late else "ok"}


def status_by_system() -> dict[int, list[dict]]:
    with get_db() as connection:
        rows = connection.execute(select(collectors)).mappings().all()
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["system_id"]].append(view(r))
    return out

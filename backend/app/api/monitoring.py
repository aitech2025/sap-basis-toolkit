"""Live monitoring API: collectors, monitors, incidents, alert rules and maintenance windows."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from ..auth import Principal, authorize, require
from ..services import configuration, incidents, monitors, systems
from ..services.collectors import CollectorError, runner
from ..services.tabular import InputFormatError
from .toolkit import load_system, read_body

router = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])

MONITOR_METRICS = {
    "availability": ["availability.system_up", "availability.instances_down"],
    "host": ["host.load1", "host.memory_used_pct", "host.disk_used_pct"],
    "workprocesses": ["wp.dia_busy_pct", "wp.btc_busy_pct", "wp.long_running", "wp.stopped"],
    "dumps": ["dumps.last_hour", "dumps.last_24h"],
    "syslog": ["syslog.errors_last_hour"],
    "updates": ["updates.errors"],
    "trfc": ["trfc.errors"],
    "qrfc": ["qrfc.blocked_queues"],
    "idocs": ["idoc.errors_24h"],
    "hana": ["hana.memory_used_pct", "hana.cpu_pct", "hana.disk_used_pct", "hana.data_backup_age_h", "hana.log_backup_age_h"],
}
UPLOADABLE = ("dumps", "syslog", "updates", "trfc", "qrfc", "idocs")


def _principal(request: Request) -> Principal:
    return request.state.principal


# ------------------------------------------------------------------------------- collectors
class CollectorIn(BaseModel):
    type: str = Field(pattern="^(sapcontrol|hana|node_exporter)$")
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    use_tls: bool = True
    verify_tls: bool = True
    username: str | None = Field(default=None, max_length=120)
    password: str | None = Field(default=None, max_length=512)
    database_name: str | None = Field(default=None, max_length=64)
    abap_schema: str | None = Field(default=None, max_length=64)
    interval_seconds: int = Field(default=300, ge=30, le=86400)
    enabled: bool = True


class CollectorPatch(BaseModel):
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    use_tls: bool | None = None
    verify_tls: bool | None = None
    username: str | None = Field(default=None, max_length=120)
    password: str | None = Field(default=None, max_length=512)
    clear_password: bool | None = None
    database_name: str | None = Field(default=None, max_length=64)
    abap_schema: str | None = Field(default=None, max_length=64)
    interval_seconds: int | None = Field(default=None, ge=30, le=86400)
    enabled: bool | None = None


def _collector_errors(fn, *args):
    try:
        return fn(*args)
    except CollectorError as exc:
        raise HTTPException(422, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/systems/{system_id}/collectors")
def list_collectors(system_id: int):
    load_system(system_id)
    return runner.list_for_system(system_id)


@router.post("/systems/{system_id}/collectors", status_code=201)
def create_collector(system_id: int, payload: CollectorIn):
    system = load_system(system_id)
    if payload.type == "hana" and system["database"] != "HANA":
        raise HTTPException(422, f"{system['sid']} runs on {system['database']}, not HANA")
    return _collector_errors(runner.create, system_id, payload.model_dump())


@router.post("/systems/{system_id}/collectors/test")
def test_collector(system_id: int, payload: CollectorIn):
    load_system(system_id)
    return _collector_errors(runner.test_connection, system_id, payload.model_dump())


@router.patch("/collectors/{collector_id}")
def update_collector(collector_id: int, payload: CollectorPatch):
    return _collector_errors(runner.update_collector, collector_id, payload.model_dump(exclude_unset=True))


@router.delete("/collectors/{collector_id}", status_code=204)
def delete_collector(collector_id: int):
    _collector_errors(runner.delete, collector_id)
    return Response(status_code=204)


@router.post("/collectors/{collector_id}/run")
def run_collector(collector_id: int):
    """Run now (operators may trigger runs; changing collectors needs admin)."""
    _collector_errors(runner.get, collector_id)
    return runner.run(collector_id)


# ------------------------------------------------------------------------------- monitors
@router.get("/systems/{system_id}/monitors")
def system_monitors(system_id: int):
    load_system(system_id)
    latest = monitors.latest(system_id)
    return {"monitors": latest, "collectors": runner.list_for_system(system_id),
            "incidents": incidents.counts(system_id)}


@router.get("/systems/{system_id}/monitors/{monitor}")
def system_monitor(system_id: int, monitor: str, hours: int = Query(24, ge=1, le=24 * 30)):
    if monitor not in monitors.MONITORS:
        raise HTTPException(404, "Unknown monitor")
    load_system(system_id)
    latest = monitors.latest(system_id, monitor).get(monitor)
    return {"monitor": monitor, "latest": latest,
            "series": monitors.series(system_id, MONITOR_METRICS[monitor], hours), "uploadable": monitor in UPLOADABLE}


@router.post("/systems/{system_id}/monitors/{monitor}/upload", status_code=201)
async def upload_monitor(request: Request, system_id: int, monitor: str):
    """Fallback for systems without a HANA collector: ST22, SM21, SM13, SM58, SMQ1/SMQ2, WE02 exports."""
    if monitor not in UPLOADABLE:
        raise HTTPException(404, f"Uploads are supported for: {', '.join(UPLOADABLE)}")
    system = load_system(system_id)
    if system["stack"] == "JAVA":
        raise HTTPException(409, "These ABAP monitors do not apply to Java systems")
    body = await read_body(request)
    try:
        data, metrics, health = monitors.parse_upload(monitor, body, systems.system_zone(system))
    except InputFormatError as exc:
        raise HTTPException(422, str(exc))
    monitors.store(system_id, monitor, "upload", data, health)
    monitors.record_metrics(system_id, metrics)
    evaluation = incidents.evaluate(system, metrics)
    return {"monitor": monitor, "health": health, "summary": data.get("summary"), "metrics": metrics, "incidents": evaluation}


# ---------------------------------------------------------------- configuration & security analysis
class ConfigurationSnapshotIn(BaseModel):
    source: str = Field(default="manual", min_length=1, max_length=40)
    snapshot: dict = Field(min_length=1)
    set_baseline: bool = False


@router.post("/systems/{system_id}/configuration/snapshots", status_code=201)
def capture_configuration(system_id: int, payload: ConfigurationSnapshotIn, request: Request):
    """Store a sanitized configuration snapshot; only admins may replace the baseline."""
    load_system(system_id)
    if payload.set_baseline and not _principal(request).can("admin"):
        raise HTTPException(403, "admin role required to establish a configuration baseline")
    try:
        return configuration.capture(system_id, payload.source, payload.snapshot, payload.set_baseline)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/systems/{system_id}/configuration/snapshots")
def configuration_history(system_id: int, limit: int = Query(50, ge=1, le=500)):
    load_system(system_id)
    return configuration.list_snapshots(system_id, limit)


@router.get("/systems/{system_id}/configuration/drift")
def configuration_drift(system_id: int):
    load_system(system_id)
    return configuration.drift(system_id)


@router.post("/systems/{system_id}/configuration/baseline", status_code=201,
             dependencies=[Depends(require("admin"))])
def set_configuration_baseline(system_id: int, payload: ConfigurationSnapshotIn):
    """Admin-only baseline replacement; credentials and secret-like fields are redacted before storage."""
    load_system(system_id)
    try:
        return configuration.capture(system_id, payload.source, payload.snapshot, True)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# ------------------------------------------------------------------------------- incidents
class IncidentAction(BaseModel):
    message: str | None = Field(default=None, max_length=4000)
    assignee: str | None = Field(default=None, max_length=64)


@router.get("/incidents")
def list_incidents(status: str | None = Query("active", pattern="^(active|open|acknowledged|resolved|all)$"),
                   system_id: int | None = None, severity: str | None = Query(None, pattern="^(warning|critical)$"),
                   limit: int = Query(200, ge=1, le=2000)):
    return incidents.list_incidents(None if status == "all" else status, system_id, severity, limit)


@router.get("/incidents/counts")
def incident_counts(system_id: int | None = None):
    return incidents.counts(system_id)


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: int):
    try:
        return incidents.get_incident(incident_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/incidents/{incident_id}/{action}")
def incident_action(incident_id: int, action: str, payload: IncidentAction, request: Request):
    if action not in ("acknowledge", "resolve", "reopen", "assign", "comment"):
        raise HTTPException(404, "Unknown action")
    try:
        return incidents.act(incident_id, action, _principal(request).username, payload.message, payload.assignee)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))


# ------------------------------------------------------------------------------- rules
class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    metric: str = Field(min_length=1, max_length=80)
    operator: str = Field(pattern="^(>|>=|<|<=)$")
    warning: float | None = None
    critical: float | None = None
    scope_type: str = Field(default="global", pattern="^(global|landscape|system)$")
    scope_value: str | None = Field(default=None, max_length=80)
    enabled: bool = True
    description: str | None = Field(default=None, max_length=1000)


class RulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    operator: str | None = Field(default=None, pattern="^(>|>=|<|<=)$")
    warning: float | None = None
    critical: float | None = None
    enabled: bool | None = None
    description: str | None = Field(default=None, max_length=1000)


@router.get("/rules")
def list_rules():
    return incidents.list_rules()


@router.get("/rules/metrics")
def rule_metrics():
    return [{"metric": m, "name": n, "operator": o, "warning": w, "critical": c, "description": d}
            for m, n, o, w, c, d in incidents.BUILTIN_RULES]


@router.post("/rules", status_code=201)
def create_rule(payload: RuleIn):
    known = {m for m, *_ in incidents.BUILTIN_RULES}
    if payload.metric not in known:
        raise HTTPException(422, f"Unknown metric; choose one of {', '.join(sorted(known))}")
    try:
        return incidents.save_rule(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, payload: RulePatch):
    try:
        return incidents.save_rule(payload.model_dump(exclude_unset=True), rule_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int):
    try:
        incidents.delete_rule(rule_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return Response(status_code=204)


# ------------------------------------------------------------------------------- maintenance
class MaintenanceIn(BaseModel):
    scope_type: str = Field(pattern="^(global|landscape|system)$")
    scope_value: str | None = Field(default=None, max_length=80)
    starts_at: datetime
    ends_at: datetime
    reason: str = Field(min_length=3, max_length=500)


@router.get("/maintenance")
def list_maintenance(include_past: bool = False):
    return incidents.list_maintenance(include_past)


@router.post("/maintenance", status_code=201)
def create_maintenance(payload: MaintenanceIn, request: Request):
    data = payload.model_dump()
    for k in ("starts_at", "ends_at"):
        if data[k].tzinfo is None:
            raise HTTPException(422, f"{k} must include a timezone offset")
    try:
        return incidents.create_maintenance(data, _principal(request).username)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/maintenance/{window_id}/end")
def end_maintenance(window_id: int):
    try:
        incidents.end_maintenance(window_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"ended": window_id}

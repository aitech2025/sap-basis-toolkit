"""Multi-system API: system registry, per-system modules, landscape overview and alert history."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import config
from ..db import (
    alert_events, certificate_checks, get_db, notification_events, sap_systems, targets,
)
from ..auth import authorize
from ..services import cert_import, growth, incidents, monitors, ops, profiles, routes, systems, transports, users
from ..services.routes import RouteError, RouteNotFound
from ..services.cert_import import CertificateImportError
from ..services.systems import SystemConflict, SystemNotFound, SystemValidationError
from ..services.tabular import InputFormatError, to_csv

router = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])

CERT_SEVERITY = {"expired": 3, "critical": 3, "unreachable": 3, "blocked": 2, "warning": 2, "untrusted": 2,
                 "healthy": 0, "pending": 1}
HEALTH = {0: "healthy", 1: "unknown", 2: "warning", 3: "critical"}
Layer = Literal["application", "database", "os"]


async def read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Upload exceeds {config.MAX_UPLOAD_BYTES} bytes")
    body = await request.body()
    if len(body) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Upload exceeds {config.MAX_UPLOAD_BYTES} bytes")
    if not body.strip():
        raise HTTPException(400, "Request body is empty")
    return body


def _check_text_size(*values: str | None) -> None:
    if sum(len(v or "") for v in values) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Upload exceeds {config.MAX_UPLOAD_BYTES} bytes")


def load_system(system_id: int) -> dict:
    try:
        return systems.describe(system_id)
    except SystemNotFound as exc:
        raise HTTPException(404, str(exc))


def _require(system: dict, module: str) -> None:
    capability = system["capabilities"][module]
    if not capability["supported"]:
        raise HTTPException(409, f"{module} is not supported for {system['product_label']} on stack "
                                 f"{system['stack']}: {capability['how_to_export']}")


# ------------------------------------------------------------------ metadata & registry
class SystemIn(BaseModel):
    sid: str = Field(min_length=3, max_length=3)
    name: str = Field(min_length=1, max_length=120)
    product: str
    stack: str = "ABAP"
    database: str
    environment: str
    landscape: str = Field(default="default", max_length=80)
    product_version: str | None = Field(default=None, max_length=60)
    basis_release: str | None = Field(default=None, max_length=20)
    license_model: str | None = None
    host: str | None = Field(default=None, max_length=253)
    default_client: str | None = Field(default=None, max_length=3)
    timezone: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool = True
    control_enabled: bool | None = None
    monitoring_layers: list[Layer] | None = Field(default=None, max_length=3, description="Default: every layer")


class SystemPatch(BaseModel):
    sid: str | None = Field(default=None, min_length=3, max_length=3)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    product: str | None = None
    stack: str | None = None
    database: str | None = None
    environment: str | None = None
    landscape: str | None = Field(default=None, max_length=80)
    product_version: str | None = Field(default=None, max_length=60)
    basis_release: str | None = Field(default=None, max_length=20)
    license_model: str | None = None
    host: str | None = Field(default=None, max_length=253)
    default_client: str | None = Field(default=None, max_length=3)
    timezone: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    control_enabled: bool | None = None
    monitoring_layers: list[Layer] | None = Field(default=None, max_length=3)


def _registry_errors(fn, *args):
    try:
        return fn(*args)
    except SystemValidationError as exc:
        raise HTTPException(422, str(exc))
    except SystemConflict as exc:
        raise HTTPException(409, str(exc))
    except SystemNotFound as exc:
        raise HTTPException(404, str(exc))


@router.get("/meta/catalogue")
def catalogue():
    """Products, stacks, databases, environments and license models the registry accepts."""
    return profiles.catalogue()


@router.get("/systems")
def list_systems(enabled: bool | None = None, landscape: str | None = None,
                 environment: str | None = None, product: str | None = None):
    return systems.list_systems(enabled, landscape, environment, product)


@router.get("/landscapes")
def list_landscapes():
    return systems.landscapes()


@router.post("/systems", status_code=201)
def create_system(payload: SystemIn, request: Request):
    principal = getattr(request.state, "principal", None)
    if payload.control_enabled is not None and not (principal and principal.can("admin")):
        raise HTTPException(403, "Only admins can change whether start/stop is allowed")
    return _registry_errors(systems.create_system, payload.model_dump())


@router.get("/systems/{system_id}")
def get_system(system_id: int):
    return load_system(system_id)


@router.patch("/systems/{system_id}")
def update_system(system_id: int, payload: SystemPatch, request: Request):
    principal = getattr(request.state, "principal", None)
    if "control_enabled" in payload.model_fields_set and not (principal and principal.can("admin")):
        raise HTTPException(403, "Only admins can change whether start/stop is allowed")
    return _registry_errors(systems.update_system, system_id, payload.model_dump(exclude_unset=True))


@router.delete("/systems/{system_id}", status_code=204)
def delete_system(system_id: int):
    _registry_errors(systems.delete_system, system_id)
    return Response(status_code=204)


@router.get("/systems/{system_id}/runs")
def list_runs(system_id: int, module: str | None = None, limit: int = Query(50, ge=1, le=500)):
    load_system(system_id)
    return systems.list_runs(system_id, module, limit)


# ------------------------------------------------------------------ certificates
def _endpoint_status(system_id: int | None = None) -> list[dict]:
    latest_ids = select(
        certificate_checks.c.target_id, func.max(certificate_checks.c.id).label("check_id")
    ).group_by(certificate_checks.c.target_id).subquery()
    query = (
        select(
            targets.c.id.label("target_id"), targets.c.name, targets.c.hostname, targets.c.port,
            targets.c.system_id, sap_systems.c.sid, sap_systems.c.landscape,
            certificate_checks.c.status, certificate_checks.c.days_remaining, certificate_checks.c.not_after,
            certificate_checks.c.checked_at, certificate_checks.c.subject, certificate_checks.c.issuer,
            certificate_checks.c.error,
        )
        .select_from(targets)
        .join(sap_systems, sap_systems.c.id == targets.c.system_id, isouter=True)
        .join(latest_ids, latest_ids.c.target_id == targets.c.id, isouter=True)
        .join(certificate_checks, certificate_checks.c.id == latest_ids.c.check_id, isouter=True)
        .where(targets.c.enabled.is_(True))
    )
    if system_id is not None:
        query = query.where(targets.c.system_id == system_id)
    with get_db() as connection:
        rows = connection.execute(query).mappings().all()
    result = []
    for row in rows:
        item = dict(row)
        item["status"] = item["status"] or "pending"
        if hasattr(item.get("checked_at"), "isoformat"):
            item["checked_at"] = item["checked_at"].isoformat()
        result.append(item)
    return result


def certificate_summary(system_id: int | None = None) -> dict:
    endpoints = _endpoint_status(system_id)
    imported = cert_import.list_imported(system_id)
    counts: dict[str, int] = {}
    for item in endpoints + imported:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    by_expiry = lambda item: (item["days_remaining"] is None, item["days_remaining"] or 0)  # noqa: E731
    return {"counts": counts, "endpoints": sorted(endpoints, key=by_expiry),
            "strust_certificates": sorted(imported, key=by_expiry)}


@router.get("/certificates/summary")
def all_certificates():
    """Landscape-wide endpoint and STRUST certificate status, soonest expiry first."""
    return certificate_summary()


@router.get("/systems/{system_id}/certificates")
def system_certificates(system_id: int):
    load_system(system_id)
    return certificate_summary(system_id)


@router.post("/systems/{system_id}/certificates/import", status_code=201)
async def import_certificates(request: Request, system_id: int,
                              pse_name: str = Query(..., min_length=1, max_length=255)):
    """Upload PEM/DER/Base64 certificates exported from STRUST or Java key storage."""
    load_system(system_id)
    body = await read_body(request)
    try:
        return cert_import.import_certificates(body, system_id, pse_name.strip())
    except CertificateImportError as exc:
        raise HTTPException(422, str(exc))


@router.delete("/systems/{system_id}/certificates/{certificate_id}", status_code=204)
def delete_imported_certificate(system_id: int, certificate_id: int):
    if not cert_import.delete_imported(system_id, certificate_id):
        raise HTTPException(404, "Certificate not found for this system")
    return Response(status_code=204)


# ------------------------------------------------------------------ table growth
class CapacityIn(BaseModel):
    disk_total_gb: float = Field(gt=0)
    disk_used_gb: float = Field(ge=0)
    memory_total_gb: float | None = Field(default=None, gt=0)
    memory_used_gb: float | None = Field(default=None, ge=0)
    captured_at: datetime | None = None


@router.post("/systems/{system_id}/growth/snapshots", status_code=201)
async def upload_table_snapshot(request: Request, system_id: int, captured_at: datetime | None = None):
    """CSV body: table name + size (MB/KB/bytes) [+ row count]; see the system's export guide."""
    load_system(system_id)
    body = await read_body(request)
    try:
        return growth.store_snapshot(system_id, body, captured_at)
    except InputFormatError as exc:
        raise HTTPException(422, str(exc))


@router.post("/systems/{system_id}/growth/capacity", status_code=201)
def record_capacity(system_id: int, payload: CapacityIn):
    load_system(system_id)
    if payload.disk_used_gb > payload.disk_total_gb:
        raise HTTPException(422, "disk_used_gb cannot exceed disk_total_gb")
    if payload.memory_used_gb is not None and payload.memory_total_gb and payload.memory_used_gb > payload.memory_total_gb:
        raise HTTPException(422, "memory_used_gb cannot exceed memory_total_gb")
    gb = 1024.0
    return growth.store_capacity(
        system_id, payload.disk_total_gb * gb, payload.disk_used_gb * gb,
        payload.memory_total_gb * gb if payload.memory_total_gb else None,
        payload.memory_used_gb * gb if payload.memory_used_gb is not None else None,
        payload.captured_at,
    )


@router.get("/systems/{system_id}/growth/report")
def growth_report(system_id: int, window_days: int = Query(30, ge=1, le=730), top: int = Query(10, ge=1, le=100)):
    system = load_system(system_id)
    return growth.build_report(system_id, window_days, top, system["product"])


# ------------------------------------------------------------------ users & licenses
class UserAnalysisIn(BaseModel):
    usr02_csv: str = Field(min_length=1, description="USR02/SUIM export (ABAP) or UME user export (Java)")
    usage_csv: str | None = Field(default=None, description="Optional ST03N user/transaction export")
    dormant_days: int | None = Field(default=None, ge=1, le=3650)


@router.post("/systems/{system_id}/users/analyze")
def analyze_users(system_id: int, payload: UserAnalysisIn, format: str = Query("json", pattern="^(json|csv)$")):
    """Stateless: uploads are analysed in memory; only summary counts are kept in run history."""
    system = load_system(system_id)
    _check_text_size(payload.usr02_csv, payload.usage_csv)
    capability = system["capabilities"]["users"]
    try:
        report = users.analyse(
            payload.usr02_csv.encode(), payload.usage_csv.encode() if payload.usage_csv else None,
            dormant_days=payload.dormant_days, license_model=system["effective_license_model"],
            license_recommendations=capability["license_recommendations"],
        )
    except InputFormatError as exc:
        raise HTTPException(422, str(exc))
    summary = report["summary"]
    systems.record_run(system_id, "users", "review" if summary["lock_candidates"] or summary["license_downgrades"] else "pass",
                       {k: summary[k] for k in ("users_analysed", "active_dialog_users", "lock_candidates",
                                                "never_logged_on", "license_downgrades", "estimated_saving",
                                                "saving_unit", "license_model")})
    if format == "csv":
        rows = [{k: u[k] for k in ("user", "user_type", "user_group", "last_logon", "days_inactive", "current_license", "reason")}
                for u in report["lock_candidates"]]
        return PlainTextResponse(to_csv(rows), media_type="text/csv",
                                 headers={"Content-Disposition": f"attachment; filename={system['sid']}_lock_candidates.csv"})
    return report


# ------------------------------------------------------------------ transports
class TransportPrecheckIn(BaseModel):
    e071_csv: str = Field(min_length=1)
    e070_csv: str | None = None
    queue: list[str] | None = Field(default=None, max_length=2000)
    imported_in_target: list[str] | None = Field(default=None, max_length=100000)
    approved_tables: list[str] | None = Field(default=None, max_length=1000)


class TransportPostcheckIn(BaseModel):
    log: str = Field(min_length=1, description="STMS import history CSV (TRKORR, RC) or raw tp log text")
    alert: bool = True


@router.post("/systems/{system_id}/transports/precheck")
def transport_precheck(system_id: int, payload: TransportPrecheckIn):
    """system_id is the import TARGET (e.g. QAS or PRD); E070/E071 come from the source system."""
    system = load_system(system_id)
    _require(system, "transports")
    _check_text_size(payload.e071_csv, payload.e070_csv)
    imported = payload.imported_in_target
    from_history = False
    if imported is None:
        stored = transports.imported_requests(system_id)
        if stored:
            imported, from_history = sorted(stored), True
    try:
        result = transports.precheck(payload.e071_csv, payload.e070_csv, payload.queue,
                                     imported, payload.approved_tables)
    except InputFormatError as exc:
        raise HTTPException(422, str(exc))
    if from_history:
        result["notes"].append(f"Missing-predecessor check used the stored import history of {system['sid']} "
                               f"({len(imported)} requests imported with RC < 8).")
    systems.record_run(system_id, "transports-precheck", result["verdict"],
                       {"requests": len(result["queue"]), **result["counts"]})
    return result


@router.post("/systems/{system_id}/transports/postcheck")
def transport_postcheck(system_id: int, payload: TransportPostcheckIn):
    system = load_system(system_id)
    _require(system, "transports")
    _check_text_size(payload.log)
    try:
        result = transports.postcheck(payload.log, f"{system['sid']} ({system['landscape']})", payload.alert, system_id)
        entries = transports.parse_import_history(payload.log, systems.system_zone(system), datetime.now(timezone.utc))
    except InputFormatError as exc:
        raise HTTPException(422, str(exc))
    result["history"] = transports.store_import_history(system_id, entries)
    status = "fail" if result["failed"] else "review" if result["warnings"] else "pass"
    systems.record_run(system_id, "transports-postcheck", status,
                       {k: result[k] for k in ("requests", "failed", "warnings")})
    return result


@router.get("/systems/{system_id}/transports/history")
def transport_history(system_id: int, days: int = Query(90, ge=1, le=3650), limit: int = Query(200, ge=1, le=2000),
                      result: str | None = Query(None, pattern="^(success|warning|error|fatal|failed)$")):
    """Imports into this system, newest first (from post-check / import history uploads)."""
    system = load_system(system_id)
    _require(system, "transports")
    return transports.import_history(system_id, days, limit, result)


# ------------------------------------------------------------------ background jobs & locks
def _ops_upload(system_id: int, module: str):
    system = load_system(system_id)
    _require(system, module)
    return system


@router.post("/systems/{system_id}/jobs/snapshots", status_code=201)
async def upload_jobs(request: Request, system_id: int, captured_at: datetime | None = None):
    """SM37 or TBTCO export (CSV). Runs are upserted; new cancellations raise alerts."""
    system = _ops_upload(system_id, "jobs")
    body = await read_body(request)
    try:
        result = ops.store_jobs(system, body, systems.system_zone(system), captured_at)
    except InputFormatError as exc:
        raise HTTPException(422, str(exc))
    report = ops.job_report(system_id, 7)["summary"]
    systems.record_run(system_id, "jobs", "review" if report["cancelled_24h"] or report["long_running"] else "pass",
                       {"rows": result["rows"], "new_failures": result["new_failures"],
                        "cancelled_24h": report["cancelled_24h"], "long_running": report["long_running"]})
    return result


@router.get("/systems/{system_id}/jobs/report")
def jobs_report(system_id: int, days: int = Query(7, ge=1, le=90)):
    _ops_upload(system_id, "jobs")
    return ops.job_report(system_id, days)


@router.post("/systems/{system_id}/locks/snapshots", status_code=201)
async def upload_locks(request: Request, system_id: int, captured_at: datetime | None = None):
    """SM12 lock list export (CSV). Each upload is a snapshot; long-held locks raise alerts."""
    system = _ops_upload(system_id, "locks")
    body = await request.body()
    if len(body) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Upload exceeds {config.MAX_UPLOAD_BYTES} bytes")
    try:
        result = ops.store_locks(system, body, systems.system_zone(system), captured_at)
    except InputFormatError as exc:
        raise HTTPException(422, str(exc))
    return result


@router.get("/systems/{system_id}/locks/report")
def locks_report(system_id: int, history_days: int = Query(7, ge=1, le=90)):
    _ops_upload(system_id, "locks")
    return ops.lock_report(system_id, history_days)


# ------------------------------------------------------------------ overview & alerts
def _latest_runs() -> dict[tuple[int, str], dict]:
    latest: dict[tuple[int, str], dict] = {}
    for run in systems.list_runs(limit=500):
        latest.setdefault((run["system_id"], run["module"]), run)
    return latest


def system_health(registry: list[dict]) -> dict[int, dict]:
    """Per-system health across certificates, capacity forecast and last import."""
    certs = certificate_summary()
    runs = _latest_runs()
    incident_counts = incidents.counts_by_system()
    result = {}
    for system in registry:
        sid = system["id"]
        own = [c for c in certs["endpoints"] if c["system_id"] == sid] + \
              [c for c in certs["strust_certificates"] if c["system_id"] == sid]
        cert_level = max((CERT_SEVERITY.get(c["status"], 1) for c in own), default=None)
        days = [c["days_remaining"] for c in own if c["days_remaining"] is not None]
        forecast = growth.build_report(sid, 30, 3, system["product"])["capacity_forecast"] or {}
        thresholds = [forecast.get(k) for k in ("days_until_disk_threshold", "days_until_memory_threshold")
                      if forecast.get(k) is not None]
        soonest = min(thresholds) if thresholds else None
        growth_level = None if not forecast else 3 if soonest is not None and soonest <= 7 else \
            2 if soonest is not None and soonest <= config.GROWTH_ALERT_DAYS else 0
        post = runs.get((sid, "transports-postcheck"))
        transport_level = None if not post else {"fail": 3, "review": 2}.get(post["status"], 0)
        jobs = ops.job_report(sid, days=1) if system["stack"] != "JAVA" else None
        job_level = None if not jobs or jobs["stale"] else \
            2 if jobs["summary"]["cancelled_24h"] or jobs["summary"]["long_running"] else 0
        locks = ops.lock_report(sid, history_days=1) if system["stack"] != "JAVA" else None
        lock_level = None if not locks or locks["stale"] or not locks["summary"] else \
            3 if locks["summary"]["critical"] else 2 if locks["summary"]["long_running"] else 0
        own_incidents = incident_counts.get(sid)
        worst_incident = incidents.worst_severity(own_incidents) if own_incidents else None
        incident_level = {"critical": 3, "warning": 2}.get(worst_incident) if worst_incident else None
        live = monitors.latest(sid, "availability").get("availability")             if "application" in systems.layers_of(system) else None
        availability = live["data"]["summary"] if live and not live["stale"] else None
        availability_level = None if availability is None else 3 if not availability["system_up"] else \
            2 if availability["down"] or availability["degraded"] else 0
        levels = [lvl for lvl in (cert_level, growth_level, transport_level, job_level, lock_level, incident_level,
                                  availability_level) if lvl is not None]
        result[sid] = {
            "health": HEALTH[max(levels)] if levels else "unknown",
            "certificates": {"count": len(own), "health": HEALTH.get(cert_level, "unknown"),
                             "soonest_expiry_days": min(days) if days else None},
            "growth": {"health": HEALTH.get(growth_level, "unknown"),
                       "disk_used_pct": forecast.get("disk_used_pct"),
                       "days_until_threshold": soonest},
            "users": runs.get((sid, "users")),
            "transports": {"precheck": runs.get((sid, "transports-precheck")), "postcheck": post},
            "jobs": {"health": HEALTH.get(job_level, "unknown"),
                     "cancelled_24h": jobs["summary"]["cancelled_24h"] if jobs else None,
                     "long_running": jobs["summary"]["long_running"] if jobs else None},
            "availability": availability,
            "incidents": own_incidents or {"open": 0, "acknowledged": 0, "critical": 0, "warning": 0, "total": 0},
            "locks": {"health": HEALTH.get(lock_level, "unknown"),
                      "long_running": (locks["summary"] or {}).get("long_running") if locks else None,
                      "oldest_minutes": (locks["summary"] or {}).get("oldest_minutes") if locks else None},
        }
    return result


@router.get("/overview")
def overview(landscape: str | None = None):
    """Health of every enabled system across all modules."""
    registry = systems.list_systems(enabled=True, landscape=landscape)
    health = system_health(registry)
    result = [
        {**{k: system[k] for k in ("id", "sid", "name", "product", "product_label", "stack", "database",
                                   "environment", "landscape", "effective_license_model", "monitoring_layers")},
         **health[system["id"]]}
        for system in registry
    ]
    totals: dict[str, int] = {}
    for item in result:
        totals[item["health"]] = totals.get(item["health"], 0) + 1
    return {"totals": totals, "systems": result}


# ------------------------------------------------------------------ landscape topology & routes
class RouteIn(BaseModel):
    source_system_id: int
    target_system_id: int
    route_type: str = Field(pattern="^(consolidation|delivery)$")
    transport_layer: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=500)


def _route_errors(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except RouteNotFound as exc:
        raise HTTPException(404, str(exc))
    except RouteError as exc:
        raise HTTPException(422, str(exc))


@router.get("/landscapes/{landscape}/topology")
def landscape_topology(landscape: str):
    """Systems grouped into product lanes and DEV → QAS → PRD tiers, with transport routes and health."""
    registry = systems.list_systems(landscape=landscape)
    return _route_errors(routes.topology, landscape, system_health(registry))


@router.post("/landscapes/{landscape}/routes/suggest")
def suggest_routes(landscape: str, apply: bool = False):
    """Propose DEV → QAS → PRE → PRD routes per product; apply=true creates them."""
    proposals = routes.suggest_routes(landscape)
    if not apply:
        return {"proposals": proposals, "created": []}
    created, skipped = [], []
    for p in proposals:
        try:
            created.append(routes.create_route(p["source_system_id"], p["target_system_id"], p["route_type"],
                                               p["transport_layer"]))
        except RouteError as exc:
            skipped.append({**p, "reason": str(exc)})
    return {"proposals": proposals, "created": created, "skipped": skipped}


@router.get("/routes")
def list_routes(landscape: str | None = None, system_id: int | None = None):
    return routes.list_routes(landscape, system_id)


@router.post("/routes", status_code=201)
def create_route(payload: RouteIn):
    return _route_errors(routes.create_route, payload.source_system_id, payload.target_system_id,
                         payload.route_type, payload.transport_layer, payload.description)


@router.delete("/routes/{route_id}", status_code=204)
def delete_route(route_id: int):
    _route_errors(routes.delete_route, route_id)
    return Response(status_code=204)


@router.get("/systems/{system_id}/overview")
def system_overview(system_id: int):
    """Everything the toolkit knows about one system: health per module, transport path, recent activity."""
    system = load_system(system_id)
    health = system_health([system])[system_id]
    certs = certificate_summary(system_id)
    report = growth.build_report(system_id, 30, 5, system["product"])
    return {
        "system": system,
        **health,
        "certificates": {
            **health["certificates"],
            "counts": certs["counts"],
            "endpoints": len(certs["endpoints"]),
            "pse_certificates": len(certs["strust_certificates"]),
            "expiring": [
                {k: c.get(k) for k in ("name", "hostname", "pse_name", "subject", "status", "days_remaining")}
                for c in (certs["endpoints"] + certs["strust_certificates"])
                if c["status"] in ("expired", "critical", "warning", "unreachable")
            ][:5],
        },
        "growth": {
            **health["growth"],
            "tracked_tables": report["tracked_tables"],
            "tracked_growth_mb_per_day": report["tracked_growth_mb_per_day"],
            "memory_used_pct": (report["capacity_forecast"] or {}).get("memory_used_pct"),
            "top_tables": [{k: t[k] for k in ("table_name", "size_mb", "growth_mb_per_day")}
                           for t in report["top_growing_tables"][:3]],
            "snapshots": len(report["size_history"]),
        },
        "routes": routes.system_path(system_id),
        "transport_history": transports.import_history(system_id, 30, 5) if system["capabilities"]["transports"]["supported"] else None,
        "jobs": {**health["jobs"],
                 "report": ops.job_report(system_id, 7) if system["capabilities"]["jobs"]["supported"] else None},
        "locks": {**health["locks"],
                  "report": ops.lock_report(system_id, 1) if system["capabilities"]["locks"]["supported"] else None},
        "recent_alerts": alert_history(system_id=system_id, limit=5),
        "recent_runs": systems.list_runs(system_id, limit=5),
    }


@router.get("/systems/{system_id}/routes")
def system_routes(system_id: int):
    load_system(system_id)
    return routes.system_path(system_id)


@router.get("/alerts")
def alert_history(system_id: int | None = None, limit: int = Query(100, ge=1, le=500)):
    """Delivered and failed alerts from all modules, newest first."""
    with get_db() as connection:
        query = (
            select(alert_events.c.id, alert_events.c.system_id, alert_events.c.module, alert_events.c.channel,
                   alert_events.c.severity, alert_events.c.summary, alert_events.c.sent_at, alert_events.c.result,
                   alert_events.c.error, sap_systems.c.sid)
            .join(sap_systems, sap_systems.c.id == alert_events.c.system_id, isouter=True)
            .order_by(alert_events.c.sent_at.desc(), alert_events.c.id.desc()).limit(limit)
        )
        if system_id is not None:
            query = query.where(alert_events.c.system_id == system_id)
        module_rows = [dict(r) for r in connection.execute(query).mappings().all()]
        cert_query = (
            select(notification_events.c.id, targets.c.system_id, notification_events.c.channel,
                   notification_events.c.fingerprint, notification_events.c.sent_at, notification_events.c.result,
                   notification_events.c.error, targets.c.name, targets.c.hostname, sap_systems.c.sid)
            .join(targets, targets.c.id == notification_events.c.target_id)
            .join(sap_systems, sap_systems.c.id == targets.c.system_id, isouter=True)
            .order_by(notification_events.c.sent_at.desc(), notification_events.c.id.desc()).limit(limit)
        )
        if system_id is not None:
            cert_query = cert_query.where(targets.c.system_id == system_id)
        cert_rows = connection.execute(cert_query).mappings().all()
    events = [{**r, "id": f"m{r['id']}"} for r in module_rows]
    for r in cert_rows:
        # Fingerprints: "unreachable:<anchor>:<channel>" or "<serial>:<90d|..|7d|expired>:<channel>"
        parts = r["fingerprint"].split(":")
        if parts[0] == "unreachable":
            severity, what = "critical", "endpoint unreachable"
        else:
            label = parts[1] if len(parts) > 2 else parts[-1]
            severity = "critical" if label in ("expired", "7d") else "warning"
            what = "certificate expired" if label == "expired" else f"certificate within {label} of expiry"
        events.append({
            "id": f"c{r['id']}", "system_id": r["system_id"], "sid": r["sid"], "module": "certificate-endpoint",
            "channel": r["channel"], "severity": severity, "summary": f"{r['name']} ({r['hostname']}): {what}",
            "sent_at": r["sent_at"], "result": r["result"], "error": r["error"],
        })
    for e in events:
        if hasattr(e["sent_at"], "isoformat"):
            e["sent_at"] = e["sent_at"].isoformat()
    events.sort(key=lambda e: str(e["sent_at"]), reverse=True)
    return events[:limit]

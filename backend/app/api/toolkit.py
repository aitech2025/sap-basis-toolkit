from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .. import config
from ..security import require_api_key
from ..services import cert_import, growth, transports, users
from ..services.cert_import import CertificateImportError
from ..services.tabular import InputFormatError, to_csv

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


async def read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and int(declared) > config.MAX_UPLOAD_BYTES:
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


def _system(sap_system_id: str) -> str:
    sid = sap_system_id.strip().upper()
    if not sid.isalnum() or not 3 <= len(sid) <= 8:
        raise HTTPException(422, "sap_system_id must be 3-8 alphanumeric characters (e.g. PRD)")
    return sid


# ---------------------------------------------------------------- STRUST import
@router.post("/certificates/import", status_code=201)
async def import_strust_certificates(
    request: Request,
    sap_system_id: str = Query(..., description="SAP SID, e.g. PRD"),
    pse_name: str = Query(..., min_length=1, max_length=255, description="e.g. SSL server Standard"),
):
    """Upload PEM/DER/Base64 certificates exported from STRUST (public certificates only)."""
    body = await read_body(request)
    try:
        return cert_import.import_certificates(body, _system(sap_system_id), pse_name.strip())
    except CertificateImportError as exc:
        raise HTTPException(422, str(exc))


@router.get("/certificates/imported")
def list_strust_certificates(sap_system_id: str | None = None):
    return cert_import.list_imported(_system(sap_system_id) if sap_system_id else None)


# ---------------------------------------------------------------- Table growth
class CapacityIn(BaseModel):
    disk_total_gb: float = Field(gt=0)
    disk_used_gb: float = Field(ge=0)
    memory_total_gb: float | None = Field(default=None, gt=0)
    memory_used_gb: float | None = Field(default=None, ge=0)
    captured_at: datetime | None = None


@router.post("/growth/{sap_system_id}/snapshots", status_code=201)
async def upload_table_snapshot(request: Request, sap_system_id: str, captured_at: datetime | None = None):
    """CSV body: table name + size (MB/KB/bytes) [+ row count], e.g. DB02 or HANA M_CS_TABLES export."""
    body = await read_body(request)
    try:
        return growth.store_snapshot(_system(sap_system_id), body, captured_at)
    except InputFormatError as exc:
        raise HTTPException(422, str(exc))


@router.post("/growth/{sap_system_id}/capacity", status_code=201)
def record_capacity(sap_system_id: str, payload: CapacityIn):
    if payload.disk_used_gb > payload.disk_total_gb:
        raise HTTPException(422, "disk_used_gb cannot exceed disk_total_gb")
    gb = 1024.0
    return growth.store_capacity(
        _system(sap_system_id), payload.disk_total_gb * gb, payload.disk_used_gb * gb,
        payload.memory_total_gb * gb if payload.memory_total_gb else None,
        payload.memory_used_gb * gb if payload.memory_used_gb is not None else None,
        payload.captured_at,
    )


@router.get("/growth/systems")
def growth_systems():
    return growth.known_systems()


@router.get("/growth/{sap_system_id}/report")
def growth_report(sap_system_id: str, window_days: int = Query(30, ge=1, le=730), top: int = Query(10, ge=1, le=100)):
    return growth.build_report(_system(sap_system_id), window_days, top)


# ---------------------------------------------------------------- Users & licenses
class UserAnalysisIn(BaseModel):
    usr02_csv: str = Field(min_length=1, description="USR02/SUIM export")
    usage_csv: str | None = Field(default=None, description="Optional ST03N user/transaction export")
    dormant_days: int | None = Field(default=None, ge=1, le=3650)


@router.post("/users/analyze")
def analyze_users(payload: UserAnalysisIn, format: str = Query("json", pattern="^(json|csv)$")):
    """Stateless: the export is analysed in memory and not stored."""
    _check_text_size(payload.usr02_csv, payload.usage_csv)
    try:
        report = users.analyse(payload.usr02_csv.encode(), payload.usage_csv.encode() if payload.usage_csv else None,
                               dormant_days=payload.dormant_days)
    except InputFormatError as exc:
        raise HTTPException(422, str(exc))
    if format == "csv":
        rows = [{k: u[k] for k in ("user", "user_type", "user_group", "last_logon", "days_inactive", "current_license", "reason")}
                for u in report["lock_candidates"]]
        return PlainTextResponse(to_csv(rows), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=lock_candidates.csv"})
    return report


# ---------------------------------------------------------------- Transports
class TransportPrecheckIn(BaseModel):
    e071_csv: str = Field(min_length=1)
    e070_csv: str | None = None
    queue: list[str] | None = Field(default=None, max_length=2000)
    imported_in_target: list[str] | None = Field(default=None, max_length=100000)
    approved_tables: list[str] | None = Field(default=None, max_length=1000)


class TransportPostcheckIn(BaseModel):
    target_system: str = Field(min_length=3, max_length=8)
    log: str = Field(min_length=1, description="STMS import history CSV (TRKORR, RC) or raw tp log text")
    alert: bool = True


@router.post("/transports/precheck")
def transport_precheck(payload: TransportPrecheckIn):
    _check_text_size(payload.e071_csv, payload.e070_csv)
    try:
        return transports.precheck(payload.e071_csv, payload.e070_csv, payload.queue,
                                   payload.imported_in_target, payload.approved_tables)
    except InputFormatError as exc:
        raise HTTPException(422, str(exc))


@router.post("/transports/postcheck")
def transport_postcheck(payload: TransportPostcheckIn):
    _check_text_size(payload.log)
    try:
        return transports.postcheck(payload.log, _system(payload.target_system), payload.alert)
    except InputFormatError as exc:
        raise HTTPException(422, str(exc))

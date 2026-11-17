from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from ..db import certificate_checks, get_db, notification_events, targets
from ..schemas import TargetCreate
from ..services.checks import check_target
router = APIRouter(prefix="/api/v1")

def serialize(row):
    item = dict(row)
    for key in ("enabled", "tls_verified"):
        if key in item and item[key] is not None: item[key] = bool(item[key])
    for key, value in item.items():
        if hasattr(value, "isoformat"): item[key] = value.isoformat()
    return item

@router.post("/targets", status_code=201)
def create_target(payload: TargetCreate):
    with get_db() as connection:
        exists = connection.execute(select(targets.c.id).where(targets.c.hostname == payload.hostname, targets.c.port == payload.port)).first()
        if exists: raise HTTPException(409, "This hostname and port are already registered")
        row = connection.execute(targets.insert().values(name=payload.name, hostname=payload.hostname, port=payload.port).returning(targets)).mappings().one()
        return serialize(row)

@router.get("/targets")
def list_targets():
    with get_db() as connection: rows = connection.execute(select(targets).order_by(targets.c.name)).mappings().all()
    return [serialize(row) for row in rows]

@router.post("/targets/{target_id}/check")
def run_target_check(target_id: int):
    try: return serialize(check_target(target_id))
    except ValueError: raise HTTPException(404, "Enabled target not found")

@router.get("/checks")
def list_checks(limit: int = 100):
    if limit < 1 or limit > 500: raise HTTPException(400, "limit must be between 1 and 500")
    with get_db() as connection: rows = connection.execute(select(certificate_checks).order_by(certificate_checks.c.checked_at.desc(), certificate_checks.c.id.desc()).limit(limit)).mappings().all()
    return [serialize(row) for row in rows]

@router.get("/notifications")
def list_notifications(limit: int = 100):
    if limit < 1 or limit > 500: raise HTTPException(400, "limit must be between 1 and 500")
    with get_db() as connection: rows = connection.execute(select(notification_events).order_by(notification_events.c.sent_at.desc(), notification_events.c.id.desc()).limit(limit)).mappings().all()
    return [serialize(row) for row in rows]

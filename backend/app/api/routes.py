from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..db import get_db, certificate_checks, notification_events, targets
from ..schemas import TargetCreate, TargetPatch
from ..db import sap_systems
from ..security import require_api_key
from ..services.checks import check_target
from ..services.target_policy import TargetPolicyError, validate_target

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


def serialize(row):
    item = dict(row)
    for key in ("enabled", "tls_verified"):
        if key in item and item[key] is not None:
            item[key] = bool(item[key])
    for key, value in item.items():
        if hasattr(value, "isoformat"):
            item[key] = value.isoformat()
    return item


@router.post("/targets", status_code=201)
def create_target(payload: TargetCreate):
    try:
        validate_target(payload.hostname, payload.port)
    except TargetPolicyError as exc:
        raise HTTPException(403, str(exc))
    try:
        with get_db() as connection:
            exists = connection.execute(
                select(targets.c.id).where(
                    targets.c.hostname == payload.hostname, targets.c.port == payload.port
                )
            ).first()
            if exists:
                raise HTTPException(409, "This hostname and port are already registered")
            _require_system(connection, payload.system_id)
            row = connection.execute(
                targets.insert().values(
                    name=payload.name, hostname=payload.hostname, port=payload.port,
                    system_id=payload.system_id,
                ).returning(targets)
            ).mappings().one()
            return serialize(row)
    except IntegrityError:
        raise HTTPException(409, "This hostname and port are already registered")


def _require_system(connection, system_id):
    if system_id is None:
        return
    if connection.execute(select(sap_systems.c.id).where(sap_systems.c.id == system_id)).first() is None:
        raise HTTPException(404, f"SAP system {system_id} not found")


@router.get("/targets")
def list_targets(system_id: int | None = None):
    query = select(targets).order_by(targets.c.name)
    if system_id is not None:
        query = query.where(targets.c.system_id == system_id)
    with get_db() as connection:
        rows = connection.execute(query).mappings().all()
    return [serialize(row) for row in rows]


@router.patch("/targets/{target_id}")
def update_target(target_id: int, payload: TargetPatch):
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No changes supplied")
    with get_db() as connection:
        if "system_id" in changes:
            _require_system(connection, changes["system_id"])
        result = connection.execute(update(targets).where(targets.c.id == target_id).values(**changes))
        if result.rowcount == 0:
            raise HTTPException(404, "Target not found")
        row = connection.execute(select(targets).where(targets.c.id == target_id)).mappings().one()
    return serialize(row)


@router.patch("/targets/{target_id}/disable")
def disable_target(target_id: int):
    with get_db() as connection:
        result = connection.execute(
            update(targets).where(targets.c.id == target_id).values(enabled=False)
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Target not found")
        row = connection.execute(select(targets).where(targets.c.id == target_id)).mappings().one()
    return serialize(row)


@router.post("/targets/{target_id}/check")
def run_target_check(target_id: int):
    try:
        return serialize(check_target(target_id))
    except ValueError:
        raise HTTPException(404, "Enabled target not found")


@router.get("/checks")
def list_checks(limit: int = 100):
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit must be between 1 and 500")
    with get_db() as connection:
        rows = connection.execute(
            select(certificate_checks).order_by(
                certificate_checks.c.checked_at.desc(), certificate_checks.c.id.desc()
            ).limit(limit)
        ).mappings().all()
    return [serialize(row) for row in rows]


@router.get("/notifications")
def list_notifications(limit: int = 100):
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit must be between 1 and 500")
    with get_db() as connection:
        rows = connection.execute(
            select(notification_events).order_by(
                notification_events.c.sent_at.desc(), notification_events.c.id.desc()
            ).limit(limit)
        ).mappings().all()
    return [serialize(row) for row in rows]


@router.patch("/targets/{target_id}/enable")
def enable_target(target_id: int):
    with get_db() as connection:
        result = connection.execute(
            update(targets).where(targets.c.id == target_id).values(enabled=True)
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Target not found")
        row = connection.execute(select(targets).where(targets.c.id == target_id)).mappings().one()
    return serialize(row)

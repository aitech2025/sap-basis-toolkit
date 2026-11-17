from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from ..db import get_db, certificate_checks, notification_events, targets
from ..schemas import TargetCreate
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
            row = connection.execute(
                targets.insert().values(
                    name=payload.name, hostname=payload.hostname, port=payload.port
                ).returning(targets)
            ).mappings().one()
            return serialize(row)
    except IntegrityError:
        raise HTTPException(409, "This hostname and port are already registered")


@router.get("/targets")
def list_targets():
    with get_db() as connection:
        rows = connection.execute(select(targets).order_by(targets.c.name)).mappings().all()
    return [serialize(row) for row in rows]


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


@router.get("/certificates/summary")
def certificate_summary():
    """Latest status per enabled endpoint plus imported STRUST certificates, soonest expiry first."""
    from ..services.cert_import import list_imported

    with get_db() as connection:
        latest_ids = select(
            certificate_checks.c.target_id, func.max(certificate_checks.c.id).label("check_id")
        ).group_by(certificate_checks.c.target_id).subquery()
        rows = connection.execute(
            select(
                targets.c.id.label("target_id"), targets.c.name, targets.c.hostname, targets.c.port,
                certificate_checks.c.status, certificate_checks.c.days_remaining,
                certificate_checks.c.not_after, certificate_checks.c.checked_at, certificate_checks.c.subject,
            )
            .select_from(targets)
            .join(latest_ids, latest_ids.c.target_id == targets.c.id, isouter=True)
            .join(certificate_checks, certificate_checks.c.id == latest_ids.c.check_id, isouter=True)
            .where(targets.c.enabled.is_(True))
        ).mappings().all()
    endpoints = [serialize(row) for row in rows]
    for item in endpoints:
        item["status"] = item["status"] or "pending"
    imported = list_imported()
    counts: dict[str, int] = {}
    for item in endpoints + imported:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    by_expiry = lambda item: (item["days_remaining"] is None, item["days_remaining"] or 0)
    return {
        "counts": counts,
        "endpoints": sorted(endpoints, key=by_expiry),
        "strust_certificates": sorted(imported, key=by_expiry),
    }

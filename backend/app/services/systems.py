"""SAP system registry."""
import json
import re

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from ..db import analysis_runs, get_db, sap_systems
from . import profiles

SID_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{2}$")


class SystemNotFound(LookupError):
    pass


class SystemValidationError(ValueError):
    pass


class SystemConflict(ValueError):
    pass


def _serialize(row) -> dict:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    if hasattr(item.get("created_at"), "isoformat"):
        item["created_at"] = item["created_at"].isoformat()
    prod = profiles.product(item["product"]) or {}
    item["product_label"] = prod.get("label", item["product"])
    item["effective_license_model"] = profiles.effective_license_model(item)
    return item


def normalise(data: dict) -> dict:
    clean = dict(data)
    for key in ("sid", "product", "stack", "database", "environment", "license_model"):
        if clean.get(key) is not None:
            clean[key] = str(clean[key]).strip().upper() or None
    for key in ("name", "landscape", "product_version", "basis_release", "host", "default_client", "description"):
        if clean.get(key) is not None:
            clean[key] = str(clean[key]).strip() or None
    if "landscape" in clean and not clean["landscape"]:
        clean["landscape"] = "default"
    return clean


def _validate(full: dict) -> None:
    errors = []
    if not SID_PATTERN.match(full.get("sid") or ""):
        errors.append("SID must be 3 characters: a letter followed by letters/digits (e.g. PRD, S4P)")
    if full.get("environment") not in profiles.ENVIRONMENTS:
        errors.append(f"environment must be one of {', '.join(profiles.ENVIRONMENTS)}")
    if full.get("default_client") and not re.fullmatch(r"\d{3}", full["default_client"]):
        errors.append("default_client must be 3 digits")
    errors += profiles.validate_system(full.get("product", ""), full.get("stack", ""),
                                       full.get("database", ""), full.get("license_model"))
    if errors:
        raise SystemValidationError("; ".join(errors))


def list_systems(enabled: bool | None = None, landscape: str | None = None,
                 environment: str | None = None, product: str | None = None) -> list[dict]:
    query = select(sap_systems)
    if enabled is not None:
        query = query.where(sap_systems.c.enabled.is_(enabled))
    if landscape:
        query = query.where(sap_systems.c.landscape == landscape)
    if environment:
        query = query.where(sap_systems.c.environment == environment.upper())
    if product:
        query = query.where(sap_systems.c.product == product.upper())
    with get_db() as connection:
        rows = connection.execute(query.order_by(sap_systems.c.landscape, sap_systems.c.sid)).mappings().all()
    return [_serialize(r) for r in rows]


def get_system(system_id: int) -> dict:
    with get_db() as connection:
        row = connection.execute(select(sap_systems).where(sap_systems.c.id == system_id)).mappings().first()
    if row is None:
        raise SystemNotFound(f"SAP system {system_id} not found")
    return _serialize(row)


def describe(system_id: int) -> dict:
    system = get_system(system_id)
    system["capabilities"] = profiles.capabilities(system)
    return system


def create_system(data: dict) -> dict:
    data = normalise(data)
    data.setdefault("landscape", "default")
    _validate(data)
    try:
        with get_db() as connection:
            row = connection.execute(sap_systems.insert().values(**data).returning(sap_systems)).mappings().one()
    except IntegrityError as exc:
        raise SystemConflict(f"System {data['sid']} already exists in landscape '{data['landscape']}'") from exc
    return _serialize(row)


def update_system(system_id: int, changes: dict) -> dict:
    current = get_system(system_id)
    changes = normalise(changes)
    merged = {**current, **changes}
    _validate(merged)
    try:
        with get_db() as connection:
            connection.execute(update(sap_systems).where(sap_systems.c.id == system_id).values(**changes))
    except IntegrityError as exc:
        raise SystemConflict(f"System {merged['sid']} already exists in landscape '{merged['landscape']}'") from exc
    return get_system(system_id)


def delete_system(system_id: int) -> None:
    get_system(system_id)
    with get_db() as connection:
        connection.execute(delete(sap_systems).where(sap_systems.c.id == system_id))


def landscapes() -> list[dict]:
    with get_db() as connection:
        rows = connection.execute(
            select(sap_systems.c.landscape, func.count().label("systems"))
            .group_by(sap_systems.c.landscape).order_by(sap_systems.c.landscape)
        ).mappings().all()
    return [dict(r) for r in rows]


def record_run(system_id: int, module: str, status: str, summary: dict) -> None:
    with get_db() as connection:
        connection.execute(analysis_runs.insert().values(
            system_id=system_id, module=module, status=status, summary=json.dumps(summary, default=str)))


def list_runs(system_id: int | None = None, module: str | None = None, limit: int = 50) -> list[dict]:
    query = select(analysis_runs).order_by(analysis_runs.c.created_at.desc(), analysis_runs.c.id.desc()).limit(limit)
    if system_id is not None:
        query = query.where(analysis_runs.c.system_id == system_id)
    if module:
        query = query.where(analysis_runs.c.module == module)
    with get_db() as connection:
        rows = connection.execute(query).mappings().all()
    result = []
    for row in rows:
        item = dict(row)
        item["summary"] = json.loads(item["summary"])
        if hasattr(item["created_at"], "isoformat"):
            item["created_at"] = item["created_at"].isoformat()
        result.append(item)
    return result

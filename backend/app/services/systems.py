"""SAP system registry."""
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from ..db import analysis_runs, get_db, sap_systems
from . import profiles

SID_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{2}$")

# Monitoring layers a system can opt into. Every collector and every metric belongs to one of them.
LAYERS = ("application", "database", "os")
LAYER_LABELS = {"application": "SAP application", "database": "database", "os": "operating system"}  # mid-sentence


class SystemNotFound(LookupError):
    pass


class SystemValidationError(ValueError):
    pass


class SystemConflict(ValueError):
    pass


def normalise_layers(value) -> str | None:
    """Canonical stored form ("application,os") of a layer selection; None keeps the default (every layer)."""
    if value is None:
        return None
    items = value.split(",") if isinstance(value, str) else list(value)
    chosen = {str(i).strip().lower() for i in items if str(i).strip()}
    unknown = sorted(chosen - set(LAYERS))
    if unknown:
        raise SystemValidationError(f"Unknown monitoring layer {', '.join(unknown)}; choose from {', '.join(LAYERS)}")
    if not chosen:
        raise SystemValidationError("Select at least one monitoring layer (application, database or os)")
    return ",".join(layer for layer in LAYERS if layer in chosen)


def layers_of(system) -> set[str]:
    """Layers a system monitors, from a stored row (string or NULL) or a serialised system (list)."""
    raw = system.get("monitoring_layers")
    if not raw:
        return set(LAYERS)
    items = raw if isinstance(raw, (list, tuple, set)) else str(raw).split(",")
    return {i for i in items if i in LAYERS} or set(LAYERS)


def metric_layer(metric: str | None) -> str | None:
    """Layer a metric belongs to; None for metrics about the collectors themselves (collector.failed)."""
    if not metric or metric.startswith("collector."):
        return None
    if metric.startswith(("host.", "db.host_")):
        return "os"
    if metric.startswith(("hana.", "db.")):
        return "database"
    return "application"


def _serialize(row) -> dict:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["control_enabled"] = None if item.get("control_enabled") is None else bool(item["control_enabled"])
    item["monitoring_layers"] = [layer for layer in LAYERS if layer in layers_of(item)]
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
    for key in ("name", "landscape", "product_version", "basis_release", "host", "default_client", "description", "timezone"):
        if clean.get(key) is not None:
            clean[key] = str(clean[key]).strip() or None
    if "landscape" in clean and not clean["landscape"]:
        clean["landscape"] = "default"
    if "monitoring_layers" in clean:
        clean["monitoring_layers"] = normalise_layers(clean["monitoring_layers"])
    return clean


def _validate(full: dict) -> None:
    errors = []
    if not SID_PATTERN.match(full.get("sid") or ""):
        errors.append("SID must be 3 characters: a letter followed by letters/digits (e.g. PRD, S4P)")
    if full.get("environment") not in profiles.ENVIRONMENTS:
        errors.append(f"environment must be one of {', '.join(profiles.ENVIRONMENTS)}")
    if "/" in (full.get("landscape") or "") or "\\" in (full.get("landscape") or ""):
        errors.append("landscape must not contain slashes")
    if full.get("timezone"):
        try:
            ZoneInfo(full["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(f"Unknown timezone '{full['timezone']}' (use an IANA name such as Asia/Kolkata or Europe/Berlin)")
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
    updated = get_system(system_id)
    turned_off = layers_of(current) - layers_of(updated)
    if turned_off:
        _layers_turned_off(updated, turned_off)
    return updated


def _layers_turned_off(system: dict, layers: set[str]) -> None:
    """Close what the switched-off layers left open: their rule incidents, and failure alerts of their collectors.

    Collectors stay configured (credentials kept) and resume when the layer is switched back on."""
    from . import incidents
    from .collectors import runner

    incidents.resolve_layers(system, layers)
    runner.resolve_failures_if_clear(system)


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


def system_zone(system: dict) -> ZoneInfo:
    """Timezone in which the SAP system reports job, lock and import times (default UTC)."""
    try:
        return ZoneInfo(system.get("timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")

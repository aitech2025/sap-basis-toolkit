"""Explicit, operator-maintained SAP infrastructure dependency inventory.

This graph is descriptive evidence only. It never infers dependencies from shared hostnames,
IP addresses or matching labels. Sensitive attributes are rejected before persistence.
"""
import json
import re
from datetime import datetime, timezone

from sqlalchemy import and_, delete, or_, select

from ..db import get_db, sap_systems, topology_assets, topology_links

ASSET_KINDS = {"host", "hana", "database", "application", "rfc_destination", "middleware", "web_dispatcher", "external_service"}
RELATIONSHIPS = {"runs_on", "uses_database", "connects_to", "routes_to", "depends_on", "hosts"}
SECRET_KEY = re.compile(r"(pass(word)?|secret|token|credential|private.?key|authorization|cookie)", re.I)


class TopologyError(ValueError):
    pass


def _safe_attributes(value, depth=0):
    if depth > 4:
        raise TopologyError("attributes nesting exceeds four levels")
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 80:
                raise TopologyError("attribute keys must be strings of at most 80 characters")
            if SECRET_KEY.search(key):
                raise TopologyError(f"Sensitive attribute '{key}' is not allowed; store credentials in a dedicated secret manager")
            out[key] = _safe_attributes(item, depth + 1)
        return out
    if isinstance(value, list):
        if len(value) > 100:
            raise TopologyError("attribute lists are limited to 100 items")
        return [_safe_attributes(item, depth + 1) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > 1000:
            raise TopologyError("attribute strings are limited to 1000 characters")
        return value
    raise TopologyError("attributes must contain only JSON-compatible scalar, list and object values")


def _asset(row):
    item = dict(row)
    item["attributes"] = json.loads(item.pop("attributes_json") or "{}")
    for key in ("created_at", "updated_at"):
        if item.get(key):
            item[key] = item[key].isoformat()
    return item


def _link(row):
    item = dict(row)
    for key in ("created_at", "verified_at"):
        if item.get(key):
            item[key] = item[key].isoformat()
    return item


def list_graph(landscape: str, system_id: int | None = None) -> dict:
    with get_db() as connection:
        query = select(topology_assets).where(topology_assets.c.landscape == landscape)
        if system_id is not None:
            query = query.where(or_(topology_assets.c.system_id == system_id, topology_assets.c.system_id.is_(None)))
        rows = connection.execute(query.order_by(topology_assets.c.kind, topology_assets.c.name)).mappings().all()
        ids = [row["id"] for row in rows]
        links = []
        if ids:
            links = connection.execute(select(topology_links).where(
                and_(topology_links.c.source_asset_id.in_(ids), topology_links.c.target_asset_id.in_(ids))
            ).order_by(topology_links.c.id)).mappings().all()
    return {"landscape": landscape, "assets": [_asset(row) for row in rows],
            "links": [_link(row) for row in links]}


def create_asset(data: dict) -> dict:
    if data["kind"] not in ASSET_KINDS:
        raise TopologyError(f"kind must be one of: {', '.join(sorted(ASSET_KINDS))}")
    attrs = _safe_attributes(data.get("attributes") or {})
    encoded = json.dumps(attrs, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 8192:
        raise TopologyError("attributes payload is limited to 8 KiB")
    landscape = data["landscape"].strip()
    if not landscape:
        raise TopologyError("landscape is required")
    if data.get("port") is not None and not 1 <= data["port"] <= 65535:
        raise TopologyError("port must be between 1 and 65535")
    with get_db() as connection:
        if data.get("system_id") is not None:
            system = connection.execute(select(sap_systems).where(
                sap_systems.c.id == data["system_id"]
            )).mappings().first()
            if system is None:
                raise TopologyError("SAP system not found")
            if system["landscape"] != landscape:
                raise TopologyError("asset landscape must match its associated SAP system")
        row = connection.execute(topology_assets.insert().values(
            landscape=landscape, system_id=data.get("system_id"), kind=data["kind"],
            name=data["name"].strip(), hostname=(data.get("hostname") or "").strip().lower() or None,
            port=data.get("port"), attributes_json=encoded, source=data.get("source", "manual"),
            updated_at=datetime.now(timezone.utc),
        ).returning(topology_assets)).mappings().one()
    return _asset(row)


def create_link(data: dict) -> dict:
    if data["relationship"] not in RELATIONSHIPS:
        raise TopologyError(f"relationship must be one of: {', '.join(sorted(RELATIONSHIPS))}")
    if data["source_asset_id"] == data["target_asset_id"]:
        raise TopologyError("an asset cannot depend on itself")
    with get_db() as connection:
        rows = connection.execute(select(topology_assets).where(
            topology_assets.c.id.in_([data["source_asset_id"], data["target_asset_id"]])
        )).mappings().all()
        if len(rows) != 2:
            raise TopologyError("source or target asset not found")
        if rows[0]["landscape"] != rows[1]["landscape"]:
            raise TopologyError("dependencies must stay within one landscape")
        try:
            row = connection.execute(topology_links.insert().values(
                source_asset_id=data["source_asset_id"], target_asset_id=data["target_asset_id"],
                relationship=data["relationship"], source=data.get("source", "manual"),
                notes=(data.get("notes") or "").strip() or None,
            ).returning(topology_links)).mappings().one()
        except Exception as exc:
            if "unique" in str(exc).lower():
                raise TopologyError("this dependency already exists") from exc
            raise
    return _link(row)


def delete_asset(asset_id: int) -> None:
    with get_db() as connection:
        result = connection.execute(delete(topology_assets).where(topology_assets.c.id == asset_id))
        if not result.rowcount:
            raise LookupError("Topology asset not found")


def delete_link(link_id: int) -> None:
    with get_db() as connection:
        result = connection.execute(delete(topology_links).where(topology_links.c.id == link_id))
        if not result.rowcount:
            raise LookupError("Topology dependency not found")

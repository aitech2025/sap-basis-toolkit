"""Transport routes and landscape topology.

A route mirrors an STMS (ABAP) or CTS+ (Java) transport route: *consolidation*
from a development system into its first follow-on system, *delivery* onwards
(QAS -> PRE -> PRD, or QAS -> TRN). Routes form a directed acyclic graph that
the UI draws as DEV -> QAS -> PRD lanes per product.
"""
from collections import defaultdict

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from ..db import get_db, sap_systems, transport_routes
from . import profiles

ROUTE_TYPES = {"consolidation": "Consolidation", "delivery": "Delivery"}

# Column order of the landscape diagram; tier rank is used to forbid backwards routes.
TIERS = [
    ("SBX", "Sandbox", 0), ("DEV", "Development", 1), ("QAS", "Quality / Test", 2),
    ("TRN", "Training", 3), ("PRE", "Pre-production", 3), ("PRD", "Production", 4),
]
TIER_RANK = {key: rank for key, _, rank in TIERS}


class RouteError(ValueError):
    pass


class RouteNotFound(LookupError):
    pass


def _systems_by_id(connection, ids) -> dict[int, dict]:
    rows = connection.execute(select(sap_systems).where(sap_systems.c.id.in_(list(ids)))).mappings().all()
    return {r["id"]: dict(r) for r in rows}


def _serialize(route: dict, systems: dict[int, dict]) -> dict:
    item = dict(route)
    if hasattr(item.get("created_at"), "isoformat"):
        item["created_at"] = item["created_at"].isoformat()
    for side in ("source", "target"):
        system = systems.get(item[f"{side}_system_id"], {})
        item[f"{side}_sid"] = system.get("sid")
        item[f"{side}_landscape"] = system.get("landscape")
        item[f"{side}_environment"] = system.get("environment")
    source = systems.get(item["source_system_id"], {})
    item["mechanism"] = "CTS+" if source.get("stack") == "JAVA" else "TMS"
    return item


def _creates_cycle(connection, source_id: int, target_id: int) -> bool:
    edges = defaultdict(list)
    for s, t in connection.execute(select(transport_routes.c.source_system_id, transport_routes.c.target_system_id)):
        edges[s].append(t)
    stack, seen = [target_id], set()
    while stack:
        node = stack.pop()
        if node == source_id:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges[node])
    return False


def validate_route(source: dict, target: dict, route_type: str) -> list[str]:
    """Return non-blocking warnings; raise RouteError for invalid routes."""
    if source["id"] == target["id"]:
        raise RouteError("A route needs two different systems")
    if route_type not in ROUTE_TYPES:
        raise RouteError(f"route_type must be one of {', '.join(ROUTE_TYPES)}")
    s_rank, t_rank = TIER_RANK.get(source["environment"]), TIER_RANK.get(target["environment"])
    if s_rank is not None and t_rank is not None and t_rank < s_rank:
        raise RouteError(
            f"Route {source['sid']} ({source['environment']}) → {target['sid']} ({target['environment']}) goes "
            "backwards in the landscape; system copies/refreshes are not transport routes")
    if (source["stack"] == "JAVA") != (target["stack"] == "JAVA"):
        raise RouteError("ABAP and Java systems cannot share a transport route")
    warnings = []
    if source["landscape"] != target["landscape"]:
        warnings.append(f"{source['sid']} and {target['sid']} are in different landscapes")
    if source["product"] != target["product"]:
        warnings.append(f"{source['sid']} ({source['product']}) and {target['sid']} ({target['product']}) are different products")
    if route_type == "consolidation" and source["environment"] not in ("DEV", "SBX", "OTHER"):
        warnings.append("Consolidation routes normally start in a development system")
    return warnings


def create_route(source_id: int, target_id: int, route_type: str, transport_layer: str | None = None,
                 description: str | None = None) -> dict:
    with get_db() as connection:
        systems = _systems_by_id(connection, {source_id, target_id})
        if source_id not in systems or target_id not in systems:
            raise RouteNotFound("Source or target system not found")
        warnings = validate_route(systems[source_id], systems[target_id], route_type)
        if _creates_cycle(connection, source_id, target_id):
            raise RouteError(f"Route {systems[source_id]['sid']} → {systems[target_id]['sid']} would create a cycle")
        layer = (transport_layer or "").strip().upper() or None
        try:
            row = connection.execute(transport_routes.insert().values(
                source_system_id=source_id, target_system_id=target_id, route_type=route_type,
                transport_layer=layer, description=(description or "").strip() or None,
            ).returning(transport_routes)).mappings().one()
        except IntegrityError as exc:
            raise RouteError("This route already exists") from exc
        return {**_serialize(dict(row), systems), "warnings": warnings}


def delete_route(route_id: int) -> None:
    with get_db() as connection:
        result = connection.execute(delete(transport_routes).where(transport_routes.c.id == route_id))
    if result.rowcount == 0:
        raise RouteNotFound("Route not found")


def list_routes(landscape: str | None = None, system_id: int | None = None) -> list[dict]:
    query = select(transport_routes).order_by(transport_routes.c.id)
    if system_id is not None:
        query = query.where(or_(transport_routes.c.source_system_id == system_id,
                                transport_routes.c.target_system_id == system_id))
    with get_db() as connection:
        rows = [dict(r) for r in connection.execute(query).mappings().all()]
        ids = {r["source_system_id"] for r in rows} | {r["target_system_id"] for r in rows}
        systems = _systems_by_id(connection, ids) if ids else {}
    result = [_serialize(r, systems) for r in rows]
    if landscape:
        result = [r for r in result if landscape in (r["source_landscape"], r["target_landscape"])]
    return result


def system_path(system_id: int) -> dict:
    """Inbound/outbound routes and the layered path through a system.

    ``hops`` is a list of layers, e.g. [[S4D, S4X], [S4Q], [S4R, S4T], [S4P]], so branching
    landscapes (two development tracks, a training branch) are represented faithfully.
    """
    routes = list_routes()
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for r in routes:
        outgoing[r["source_system_id"]].append(r)
        incoming[r["target_system_id"]].append(r)
    names = {r["source_system_id"]: r["source_sid"] for r in routes} | {r["target_system_id"]: r["target_sid"] for r in routes}

    def layers(edges, key):
        result, frontier, seen = [], [system_id], {system_id}
        while frontier:
            nxt = []
            for node in frontier:
                for r in edges[node]:
                    if r[key] not in seen:
                        seen.add(r[key])
                        nxt.append(r[key])
            if nxt:
                result.append(sorted(nxt, key=lambda i: names.get(i) or ""))
            frontier = nxt
        return result

    upstream = list(reversed(layers(incoming, "source_system_id")))
    downstream = layers(outgoing, "target_system_id")
    hops = [[{"system_id": i, "sid": names.get(i)} for i in layer] for layer in upstream + [[system_id]] + downstream]
    return {
        "inbound": incoming[system_id],
        "outbound": outgoing[system_id],
        "hops": hops if len(hops) > 1 else [],
    }


def suggest_routes(landscape: str) -> list[dict]:
    """Propose DEV → QAS → (PRE) → PRD chains per product where each tier has exactly one system."""
    from .systems import list_systems

    existing = {(r["source_system_id"], r["target_system_id"]) for r in list_routes(landscape)}
    lanes = defaultdict(list)
    for system in list_systems(landscape=landscape):
        lanes[(system["product"], system["stack"] == "JAVA")].append(system)
    proposals = []
    for (_product, _java), systems in lanes.items():
        by_env = defaultdict(list)
        for s in systems:
            by_env[s["environment"]].append(s)
        chain = [by_env[env][0] for env in ("DEV", "QAS", "PRE", "PRD") if len(by_env[env]) == 1]
        if len(chain) < 2:
            continue
        for source, target in zip(chain, chain[1:]):
            if (source["id"], target["id"]) in existing:
                continue
            proposals.append({
                "source_system_id": source["id"], "source_sid": source["sid"],
                "target_system_id": target["id"], "target_sid": target["sid"],
                "route_type": "consolidation" if source["environment"] == "DEV" else "delivery",
                "transport_layer": f"Z{source['sid']}" if source["environment"] == "DEV" else None,
                "mechanism": "CTS+" if source["stack"] == "JAVA" else "TMS",
            })
        training = by_env.get("TRN", [])
        if len(training) == 1 and len(by_env["QAS"]) == 1 and (by_env["QAS"][0]["id"], training[0]["id"]) not in existing:
            proposals.append({
                "source_system_id": by_env["QAS"][0]["id"], "source_sid": by_env["QAS"][0]["sid"],
                "target_system_id": training[0]["id"], "target_sid": training[0]["sid"],
                "route_type": "delivery", "transport_layer": None,
                "mechanism": "CTS+" if training[0]["stack"] == "JAVA" else "TMS",
            })
    return proposals


def topology(landscape: str, health: dict[int, dict]) -> dict:
    """Lanes (one per product/stack) x tiers (DEV..PRD) with routes and per-system health."""
    from .systems import list_systems

    systems = list_systems(landscape=landscape)
    if not systems:
        raise RouteNotFound(f"Landscape '{landscape}' has no systems")
    ids = {s["id"] for s in systems}
    routes = list_routes(landscape)
    internal = [r for r in routes if r["source_system_id"] in ids and r["target_system_id"] in ids]
    external = [r for r in routes if r not in internal]

    lanes: dict[tuple, dict] = {}
    for s in systems:
        key = (s["product"], s["stack"] == "JAVA")
        lane = lanes.setdefault(key, {
            "key": f"{s['product']}{'-JAVA' if key[1] else ''}",
            "product": s["product"], "product_label": s["product_label"],
            "stack": "JAVA" if key[1] else "ABAP", "systems": [],
        })
        lane["systems"].append({
            **{k: s[k] for k in ("id", "sid", "name", "environment", "database", "stack", "product_version", "enabled")},
            **health.get(s["id"], {"health": "unknown"}),
        })
    env_order = [t[0] for t in TIERS] + ["OTHER"]
    for lane in lanes.values():
        lane["systems"].sort(key=lambda x: (env_order.index(x["environment"]) if x["environment"] in env_order else 99, x["sid"]))
        lane_ids = {x["id"] for x in lane["systems"]}
        lane_routes = [r for r in internal if r["source_system_id"] in lane_ids]
        lane["path"] = _path_label(lane["systems"], lane_routes)

    used_envs = {s["environment"] for s in systems}
    tiers = [{"key": k, "label": label} for k, label, _ in TIERS if k in used_envs or k in ("DEV", "QAS", "PRD")]
    if "OTHER" in used_envs:
        tiers.append({"key": "OTHER", "label": "Other"})

    for r in internal:
        target_health = health.get(r["target_system_id"], {})
        post = (target_health.get("transports") or {}).get("postcheck")
        r["last_import"] = {"status": post["status"], "at": post["created_at"], **post["summary"]} if post else None

    warnings = [f"Route {r['source_sid']} → {r['target_sid']} leaves landscape '{landscape}'" for r in external]
    for lane in lanes.values():
        lane_ids = {x["id"] for x in lane["systems"]}
        connected = {r["source_system_id"] for r in internal} | {r["target_system_id"] for r in internal}
        if len(lane["systems"]) > 1 and not (lane_ids & connected):
            warnings.append(f"{lane['product_label']} systems have no transport routes yet")
    order = sorted(lanes.values(), key=lambda lane: (lane["stack"] == "JAVA", lane["product_label"]))
    return {"landscape": landscape, "tiers": tiers, "lanes": order, "routes": internal, "warnings": warnings,
            "route_types": ROUTE_TYPES}


def _path_label(systems: list[dict], routes: list[dict]) -> str:
    """Human readable main path, e.g. 'S4D → S4Q → S4P'."""
    if not routes:
        return ""
    sid = {s["id"]: s["sid"] for s in systems}
    outgoing = defaultdict(list)
    targets = set()
    for r in routes:
        outgoing[r["source_system_id"]].append(r["target_system_id"])
        targets.add(r["target_system_id"])
    starts = [s["id"] for s in systems if s["id"] in outgoing and s["id"] not in targets] or [routes[0]["source_system_id"]]
    labels = []
    for start in starts:
        chain, node, seen = [sid.get(start, "?")], start, {start}
        while outgoing[node]:
            # follow the highest-tier branch (towards production)
            node = max(outgoing[node], key=lambda n: TIER_RANK.get(next((s["environment"] for s in systems if s["id"] == n), ""), -1))
            if node in seen:
                break
            seen.add(node)
            chain.append(sid.get(node, "?"))
        labels.append(" → ".join(chain))
    return " | ".join(labels)


def tiers_catalogue() -> list[dict]:
    return [{"key": k, "label": label, "rank": rank} for k, label, rank in TIERS] + [
        {"key": "OTHER", "label": profiles.ENVIRONMENTS["OTHER"], "rank": None}]
